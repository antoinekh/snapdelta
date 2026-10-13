#!/bin/bash
set -euo pipefail

LAB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOPOLOGY="$LAB_DIR/snapdelta-lab.clab.yml"
ROUTERS=(r1 r2 r3)
WAIT_S=${WAIT_S:-180}

usage() {
  cat <<EOF
Usage: $(basename "$0") COMMAND

Run the snapdelta lab: two FRR routers and one Nokia SR Linux in containerlab, with eBGP on each link.

Commands:
  deploy     Deploy the lab from scratch (replaces a running lab), then wait for BGP.
  destroy    Remove the lab containers and the files that containerlab created.
  wait       Wait until each router has its two BGP peers established and the 4 prefixes of the others.
  change     Apply the change: new description on r1 eth2, VLAN 20 with a second eBGP session on the r1-r2 trunk.
  scenario   deploy, snapshot every router (before), change, snapshot again (after), diff each router.
  capture    Save the outputs of every router in tests/outputs (test data).
  -h, --help Show this help.

Environment:
  SNAPDELTA_STORE  Store of the snapshots and diffs; wins over [store] location of snapdelta.toml.
  WAIT_S           Seconds to wait for the protocols (default: 180; SR Linux takes about a minute to boot).

Examples:
  $(basename "$0") scenario
  SNAPDELTA_STORE=sqlite:///tmp/lab.db $(basename "$0") scenario
  $(basename "$0") destroy
EOF
}

if [[ -t 1 ]]; then
  BOLD=$'\e[1;36m'
  RESET=$'\e[0m'
else
  BOLD=""
  RESET=""
fi

step() {
  printf '\n%s==> %s%s\n' "$BOLD" "$1" "$RESET"
}

vtysh() {
  docker exec "clab-snapdelta-lab-$1" vtysh -c "$2"
}

sr_cli() {
  docker exec "clab-snapdelta-lab-$1" sr_cli "$2"
}

snapdelta() {
  (cd "$LAB_DIR" && uv run --quiet snapdelta "$@")
}

# Each router has its two peers established and uses the loopback and the LAN of the two others.
converged() {
  local router=$1 peers routes
  if [[ $router == "r3" ]]; then  # the SR Linux router
    local neighbors
    neighbors=$(sr_cli "$router" "info from state network-instance default protocols bgp neighbor * | as json" 2>/dev/null) || return 1
    peers=$(jq '[.neighbor[] | select(."session-state" == "established")] | length' <<< "$neighbors")
    routes=$(jq '[.neighbor[]."afi-safi"[] | select(."afi-safi-name" == "ipv4-unicast") | ."active-routes" // 0] | add' <<< "$neighbors")
  else
    peers=$(vtysh "$router" "show bgp summary json" | jq '[.ipv4Unicast.peers[] | select(.state == "Established")] | length')
    routes=$(vtysh "$router" "show ip route bgp json" | jq 'length')
  fi
  (( peers == 2 && routes == 4 ))
}

wait_for() {
  local description=$1 check=$2 waited=0
  until $check; do
    if (( waited >= WAIT_S )); then
      echo "error: $description after ${WAIT_S}s" >&2
      exit 1
    fi
    sleep 2
    (( waited += 2 ))
  done
  echo "$description after ${waited}s"
}

all_converged() {
  local router
  for router in "${ROUTERS[@]}"; do
    converged "$router" || return 1
  done
}

vlan20_up() {
  vtysh r1 "show bgp neighbors 10.12.20.2 json" | jq -e '."10.12.20.2".bgpState == "Established"' >/dev/null
}

deploy() {
  step "Deploy the lab"
  (cd "$LAB_DIR" && containerlab deploy -t "$TOPOLOGY" --reconfigure)
  step "Wait for BGP"
  wait_for "BGP established with routes on every router" all_converged
}

destroy() {
  step "Destroy the lab"
  (cd "$LAB_DIR" && containerlab destroy -t "$TOPOLOGY" --cleanup)
}

add_vlan20() {
  local router=$1 asn=$2 address=$3 peer=$4 peer_asn=$5 peer_name=$6
  docker exec "clab-snapdelta-lab-$router" ip link add link eth1 name eth1.20 type vlan id 20
  docker exec "clab-snapdelta-lab-$router" ip link set eth1.20 up
  docker exec "clab-snapdelta-lab-$router" vtysh -c "configure terminal" -c "interface eth1.20" \
    -c "description trunk to the other router, vlan 20" -c "ip address $address" -c "exit" \
    -c "router bgp $asn" -c "neighbor $peer remote-as $peer_asn" -c "neighbor $peer description $peer_name"
}

change() {
  step "Change the description of r1 eth2"
  docker exec clab-snapdelta-lab-r1 vtysh -c "configure terminal" -c "interface eth2" \
    -c "description to r3, new circuit"
  step "Add VLAN 20 on the r1-r2 trunk, with a second eBGP session"
  add_vlan20 r1 65001 10.12.20.1/24 10.12.20.2 65002 r2
  add_vlan20 r2 65002 10.12.20.2/24 10.12.20.1 65001 r1
  wait_for "BGP session up on eth1.20" vlan20_up
}

snapshot_all() {
  local label=$1 line
  declare -gA SAVED
  while read -r line; do
    echo "$line"
    if [[ $line == saved\ * ]]; then
      local reference=${line#saved }
      SAVED[${reference%%/*}]=$reference
    fi
  done < <(snapdelta snapshot --all-targets --label "$label")
}

scenario() {
  deploy
  step "Snapshot every router (before)"
  snapshot_all before
  declare -A BEFORE
  local router
  for router in "${ROUTERS[@]}"; do
    BEFORE[$router]=${SAVED[$router]}
  done
  change
  step "Snapshot every router (after)"
  snapshot_all after
  for router in "${ROUTERS[@]}"; do
    step "Diff $router: before -> after"
    local status=0
    snapdelta diff "${BEFORE[$router]}" "${SAVED[$router]}" || status=$?
    if (( status == 2 )); then
      exit 2
    fi
  done
  step "Done"
  local location
  location=$(snapdelta info | sed -n 's/^location: //p')
  echo "Store: $location. Web UI: cd $LAB_DIR && uv run snapdelta-web"
}

capture() {
  step "Capture the outputs of every router"
  (cd "$LAB_DIR" && uv run --quiet snapdelta-lab-capture tests/outputs)
}

case ${1:-} in
  deploy) deploy ;;
  destroy) destroy ;;
  wait) wait_for "BGP established with routes on every router" all_converged ;;
  change) change ;;
  scenario) scenario ;;
  capture) capture ;;
  -h | --help) usage ;;
  *)
    usage >&2
    exit 2
    ;;
esac
