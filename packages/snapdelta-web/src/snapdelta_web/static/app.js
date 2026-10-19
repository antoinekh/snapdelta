(function () {
  "use strict";

  var root = document.documentElement;
  var media = window.matchMedia("(prefers-color-scheme: dark)");

  media.addEventListener("change", function () {
    if (!localStorage.getItem("theme")) root.dataset.theme = media.matches ? "dark" : "light";
  });

  document.addEventListener("click", function (event) {
    if (event.target.closest("[data-back]")) {
      history.back();
      return;
    }
    if (event.target.closest("[data-theme-toggle]")) {
      var next = root.dataset.theme === "dark" ? "light" : "dark";
      localStorage.setItem("theme", next);
      root.dataset.theme = next;
      return;
    }
    var target = event.target.closest("[data-target-option]");
    if (target) {
      target.parentElement.querySelectorAll("[data-target-option]").forEach(function (item) {
        item.classList.toggle("active", item === target);
      });
      return;
    }
    var link = event.target.closest("[data-model-link]");
    var filters = document.getElementById("filters");
    if (link && filters && !event.ctrlKey && !event.metaKey && !event.shiftKey) {
      // The filter form loads the cards, so a new model keeps the status filters and the search.
      event.preventDefault();
      filters.elements.model.value = link.dataset.modelLink;
      filters.requestSubmit();
      document.querySelectorAll("[data-model-link]").forEach(function (item) {
        item.classList.toggle("active", item === link);
      });
    }
  });

  var palette = document.getElementById("palette");

  function openPalette() {
    if (!palette || palette.open) return;
    var input = palette.querySelector(".palette-input");
    input.value = "";
    palette.showModal();
    input.focus();
    htmx.trigger(input, "palette-open");
  }

  function movePalette(step) {
    var items = Array.prototype.slice.call(palette.querySelectorAll(".palette-item"));
    if (!items.length) return;
    var current = items.findIndex(function (item) { return item.classList.contains("active"); });
    var next = current < 0 ? (step > 0 ? 0 : items.length - 1) : (current + step + items.length) % items.length;
    items.forEach(function (item, index) {
      item.classList.toggle("active", index === next);
      item.setAttribute("aria-selected", index === next ? "true" : "false");
    });
    items[next].scrollIntoView({ block: "nearest" });
  }

  document.addEventListener("click", function (event) {
    if (event.target.closest("[data-palette-open]")) openPalette();
    else if (event.target === palette) palette.close();
  });

  document.addEventListener("keydown", function (event) {
    if (event.key.toLowerCase() === "k" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      openPalette();
      return;
    }
    if (!palette || !palette.open) return;
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      movePalette(event.key === "ArrowDown" ? 1 : -1);
    } else if (event.key === "Enter") {
      var chosen = palette.querySelector(".palette-item.active") || palette.querySelector(".palette-item");
      if (chosen) {
        event.preventDefault();
        location.href = chosen.href;
      }
    }
  });

  var CHANGED = '#cards .card[data-status="only-left"], #cards .card[data-status="only-right"], #cards .card[data-status="changed"]';
  var mapPending = false;

  function drawChangeMap() {
    mapPending = false;
    var map = document.getElementById("change-map");
    if (!map) return;
    var height = document.documentElement.scrollHeight;
    var marks = document.createDocumentFragment();
    document.querySelectorAll(CHANGED).forEach(function (card) {
      var box = card.getBoundingClientRect();
      if (!box.height) return;
      var mark = document.createElement("span");
      mark.className = "change-mark " + card.dataset.status;
      if (card.querySelector(":scope > .review.done")) mark.classList.add("accepted");
      mark.style.top = ((box.top + window.scrollY) * 100) / height + "%";
      mark.style.height = (box.height * 100) / height + "%";
      mark.dataset.card = card.id;
      marks.appendChild(mark);
    });
    map.replaceChildren(marks);
    map.hidden = !map.childElementCount;
  }

  function scheduleChangeMap() {
    if (mapPending) return;
    mapPending = true;
    requestAnimationFrame(drawChangeMap);
  }

  var cardList = document.getElementById("cards");
  if (cardList && window.ResizeObserver) new ResizeObserver(scheduleChangeMap).observe(cardList);
  window.addEventListener("resize", scheduleChangeMap);
  document.addEventListener("htmx:afterSettle", scheduleChangeMap);

  document.addEventListener("click", function (event) {
    var mark = event.target.closest(".change-mark");
    if (!mark) return;
    var card = document.getElementById(mark.dataset.card);
    card.scrollIntoView({ block: "start" });
    card.focus({ preventScroll: true });
  });

  // The reload keeps what was typed, because the ticket decides which snapshots the labels name.
  function reloadNewDiff(form, left, right) {
    var url = new URL("/new", location.origin);
    if (left) url.searchParams.set("left", left);
    if (right) url.searchParams.set("right", right);
    ["ticket", "note", "profile"].forEach(function (name) {
      var field = form.elements[name];
      if (field && field.value) url.searchParams.set(name, field.value);
    });
    location.href = url.href;
  }

  function sideValue(form, side) {
    var checked = form.querySelector('input[name="' + side + '"]:checked');
    return checked ? checked.value : "";
  }

  document.addEventListener("click", function (event) {
    var form = document.querySelector("form.new-diff");
    if (!form) return;
    if (!event.target.closest("[data-swap-sides]")) return;
    reloadNewDiff(form, sideValue(form, "right"), sideValue(form, "left"));
  });

  // The After side follows the target of the Before side, until the user picks another target for it.
  document.addEventListener("click", function (event) {
    var option = event.target.closest("#left-targets [data-target-option]");
    var form = document.querySelector("form.new-diff");
    if (!option || !form) return;
    var before = sideValue(form, "left").split(/[@/]/)[0];
    var after = sideValue(form, "right").split(/[@/]/)[0];
    if (after && after !== before) return;
    var name = option.textContent.trim();
    htmx.ajax("GET", "/targets/" + encodeURIComponent(name) + "/snapshots?side=right", "#right-snapshots");
    document.querySelectorAll("#right-targets [data-target-option]").forEach(function (item) {
      item.classList.toggle("active", item.textContent.trim() === name);
    });
  });

  document.addEventListener("change", function (event) {
    if (!event.target.matches('.targets-picker [name="group"]')) return;
    document.querySelectorAll("[data-members]").forEach(function (line) {
      line.hidden = line.dataset.members !== event.target.value;
    });
  });

  // The clipboard API needs a secure context; over plain HTTP on the network, a hidden text area copies instead.
  function copyText(text, element) {
    function done() {
      element.dataset.copied = "";
      setTimeout(function () { delete element.dataset.copied; }, 1200);
    }
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(done);
      return;
    }
    var area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.opacity = "0";
    document.body.appendChild(area);
    area.select();
    document.execCommand("copy");
    area.remove();
    done();
  }

  function objectLink(card) {
    var link = card.querySelector(":scope > .card-head [data-copy-link]");
    var url = new URL(location.href);
    var filters = document.getElementById("filters");
    ["status", "filtered", "offset"].forEach(function (key) { url.searchParams.delete(key); });
    if (filters) url.searchParams.set("model", filters.elements.model.value);
    url.searchParams.set("q", link.dataset.copyLink);
    url.hash = card.id;
    return url.href;
  }

  document.addEventListener("click", function (event) {
    var link = event.target.closest("[data-copy-link]");
    if (link) {
      event.preventDefault();
      copyText(objectLink(link.closest(".card")), link);
      return;
    }
    var api = event.target.closest("[data-copy-api]");
    if (api) {
      var auth = api.dataset.token ? ' -H "Authorization: Bearer $SNAPDELTA_TOKEN"' : "";
      copyText("curl -s" + auth + " '" + location.origin + api.dataset.copyApi + "'", api);
    }
  });

  function toggleSwitch(selector) {
    var box = document.querySelector(selector);
    if (!box) return;
    box.checked = !box.checked;
    box.dispatchEvent(new Event("change", { bubbles: true }));
  }

  // One-key shortcuts; the list is in the Shortcuts window of every page.
  document.addEventListener("keydown", function (event) {
    if (event.ctrlKey || event.metaKey || event.altKey || event.target.closest("input, select, textarea, dialog")) return;
    var focused = document.activeElement.closest("#cards .card");
    if (event.key === "?") {
      document.getElementById("shortcuts").showModal();
    } else if (event.key === "/") {
      var search = document.querySelector("main input[type=search]");
      if (!search) return;
      search.focus();
      search.select();
    } else if (event.key === "j" || event.key === "k") {
      var cards = Array.prototype.slice.call(document.querySelectorAll("#cards > .card"));
      if (!cards.length) return;
      var current = cards.indexOf(document.activeElement.closest("#cards > .card"));
      var next = event.key === "j" ? Math.min(current + 1, cards.length - 1) : Math.max(current - 1, 0);
      cards[next].focus();
    } else if (event.key === "a" && focused) {
      var review = focused.querySelector(":scope > .review form");
      if (review) htmx.trigger(review, "submit");
    } else if (event.key === "c" && focused) {
      copyText(objectLink(focused), focused.querySelector(":scope > .card-head [data-copy-link]"));
    } else if (event.key === "o") {
      toggleSwitch("[data-only-changed]");
    } else if (event.key === "h") {
      toggleSwitch("[data-hide-accepted]");
    } else {
      return;
    }
    event.preventDefault();
  });

  document.addEventListener("click", function (event) {
    if (event.target.closest("[data-shortcuts-open]")) document.getElementById("shortcuts").showModal();
  });

  // The timeline compares two snapshots: the older pick is the left side, the newer the right side.
  function timelinePicked(form, newest) {
    var boxes = Array.prototype.slice.call(form.querySelectorAll("[data-timeline-pick]"));
    var picked = boxes.filter(function (box) { return box.checked; });
    if (picked.length > 2) {
      picked.filter(function (box) { return box !== newest; }).slice(0, picked.length - 2).forEach(function (box) {
        box.checked = false;
      });
      picked = boxes.filter(function (box) { return box.checked; });
    }
    var first = boxes.indexOf(picked[0]);
    var last = picked.length === 2 ? boxes.indexOf(picked[1]) : -1;
    boxes.forEach(function (box, index) {
      var slot = box.closest(".timeline-slot");
      slot.classList.toggle("pick-left", index === first && last >= 0);
      slot.classList.toggle("pick-right", index === last);
      slot.classList.toggle("range-start", index === first && last >= 0);
      slot.classList.toggle("range-mid", index > first && index < last);
      slot.classList.toggle("range-end", index === last);
    });
    var button = form.querySelector("[data-timeline-compare]");
    form.elements.left.value = last >= 0 ? picked[0].value : "";
    form.elements.right.value = last >= 0 ? picked[1].value : "";
    button.disabled = last < 0;
    button.textContent = last >= 0
      ? "Compare " + picked[0].dataset.caption + " → " + picked[1].dataset.caption
      : picked.length ? "Select one more" : "Select two snapshots";
  }

  document.addEventListener("click", function (event) {
    var step = event.target.closest(".timeline-step");
    if (!step) return;
    var form = step.closest("[data-timeline]");
    form.querySelectorAll("[data-timeline-pick]").forEach(function (box) {
      box.checked = box.value === step.dataset.left || box.value === step.dataset.right;
    });
    timelinePicked(form, null);
  });

  document.addEventListener("change", function (event) {
    if (event.target.matches("[data-timeline-pick]")) timelinePicked(event.target.closest("[data-timeline]"), event.target);
  });

  // A faded edge tells that the timeline holds more snapshots on that side.
  function markTrackEdges(track) {
    track.classList.toggle("more-left", track.scrollLeft > 1);
    track.classList.toggle("more-right", track.scrollLeft + track.clientWidth < track.scrollWidth - 1);
  }

  // The newest snapshots are on the right: a long timeline opens scrolled to its end.
  document.addEventListener("htmx:afterSettle", function (event) {
    var track = event.target.querySelector && event.target.querySelector("[data-timeline-track]");
    if (!track) return;
    track.scrollLeft = track.scrollWidth;
    markTrackEdges(track);
  });

  // A scroll event does not bubble, so the listener captures it.
  document.addEventListener("scroll", function (event) {
    if (event.target.matches && event.target.matches("[data-timeline-track]")) markTrackEdges(event.target);
  }, true);

  // The name of the reviewer stays in this browser, so that each Accept carries it.
  function restoreReviewer() {
    var field = document.querySelector("[data-reviewer]");
    if (field && !field.value) field.value = localStorage.getItem("reviewer") || "";
  }
  restoreReviewer();
  document.addEventListener("input", function (event) {
    if (event.target.matches("[data-reviewer]")) localStorage.setItem("reviewer", event.target.value.trim());
  });

  document.addEventListener("change", function (event) {
    if (event.target.matches("[data-hide-accepted]")) document.body.classList.toggle("hide-accepted", event.target.checked);
  });

  document.addEventListener("change", function (event) {
    if (event.target.matches("[data-only-changed]")) document.body.classList.toggle("only-changed", event.target.checked);
  });

  // A target picker keeps the ticked targets in its chips, apart from the matches that its search lists.
  document.addEventListener("change", function (event) {
    var box = event.target;
    var picker = box.closest("[data-target-picker]");
    if (!picker) return;
    var selected = picker.querySelector("[data-picked-targets]");
    if (box.matches("[data-clears-targets]")) selected.replaceChildren();
    if (!box.matches(".target-pick input[type=checkbox]")) return;
    if (box.checked && !selected.contains(box)) selected.appendChild(box.closest(".target-pick"));
    else if (!box.checked && selected.contains(box)) {
      box.closest(".target-pick").remove();
      // The server leaves selected targets out of the list, so it must send the list again.
      var search = picker.querySelector("[data-target-search]");
      if (search) htmx.trigger(search, "refresh");
    }
  });

  // A form with data-track-changes says when it holds unsaved changes, and asks before the page is left.
  var dirtyForm = null;

  function markDirty(event) {
    var form = event.target.closest && event.target.closest("form[data-track-changes]");
    if (!form || form === dirtyForm) return;
    dirtyForm = form;
    var bar = form.querySelector(".save-bar");
    if (bar) bar.classList.add("dirty");
    var status = form.querySelector("[data-dirty-status]");
    if (status) status.hidden = false;
  }

  document.addEventListener("input", markDirty);
  document.addEventListener("change", markDirty);

  window.addEventListener("beforeunload", function (event) {
    if (dirtyForm) event.preventDefault();
  });

  document.addEventListener("keydown", function (event) {
    if (event.key !== "s" || !(event.ctrlKey || event.metaKey)) return;
    var form = document.querySelector("form[data-track-changes]");
    if (!form) return;
    event.preventDefault();
    form.requestSubmit();
  });

  document.addEventListener("change", function (event) {
    if (!event.target.matches("[data-select-all]")) return;
    event.target.form.querySelectorAll("tbody input[type=checkbox]").forEach(function (box) {
      box.checked = event.target.checked;
    });
  });

  document.addEventListener("submit", function (event) {
    if (event.target === dirtyForm) dirtyForm = null;
    var field = event.target.dataset.confirmSelected;
    if (field && !(event.submitter && event.submitter.hasAttribute("data-no-confirm"))) {
      var count = event.target.querySelectorAll('input[name="' + field + '"]:checked').length;
      if (count && !window.confirm("Delete " + count + " selected?")) event.preventDefault();
      return;
    }
    var message = event.target.dataset.confirm;
    if (message && !window.confirm(message)) {
      event.preventDefault();
      return;
    }
    var busy = event.target.querySelector("[data-busy]");
    if (busy) {
      busy.disabled = true;
      busy.textContent = busy.dataset.busy;
    }
  });
})();
