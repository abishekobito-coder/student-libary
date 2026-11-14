/* Personal Study Library – small vanilla JavaScript helpers (no frameworks). */
(function () {
  "use strict";

  var csrfMeta = document.querySelector('meta[name="csrf-token"]');
  var CSRF = csrfMeta ? csrfMeta.content : "";

  function storageGet(key, fallback) {
    try { return localStorage.getItem(key) || fallback; } catch (e) { return fallback; }
  }
  function storageSet(key, value) {
    try { localStorage.setItem(key, value); } catch (e) { /* ignore */ }
  }

  /* ---------- Sidebar (mobile) ---------- */
  document.querySelectorAll("[data-toggle-sidebar]").forEach(function (el) {
    el.addEventListener("click", function () {
      var sidebar = document.getElementById("sidebar");
      var backdrop = document.querySelector(".backdrop");
      if (sidebar) sidebar.classList.toggle("open");
      if (backdrop) backdrop.classList.toggle("open");
    });
  });

  /* ---------- Dialogs ---------- */
  function openDialog(opener) {
    var dlg = document.getElementById(opener.dataset.dialog);
    if (!dlg) return;
    var form = dlg.querySelector("form");
    if (form) {
      if (opener.dataset.action) form.action = opener.dataset.action;
      var title = dlg.querySelector("[data-dialog-title]");
      if (title && opener.dataset.title) title.textContent = opener.dataset.title;
      var submit = dlg.querySelector("[data-dialog-submit]");
      if (submit && opener.dataset.submit) submit.textContent = opener.dataset.submit;
      if (opener.dataset.fields) {
        var fields = JSON.parse(opener.dataset.fields);
        // Optional fields (e.g. URL) are only shown when the opener provides them.
        dlg.querySelectorAll("[data-field]").forEach(function (wrap) {
          var present = Object.prototype.hasOwnProperty.call(fields, wrap.dataset.field);
          wrap.hidden = !present;
          wrap.querySelectorAll("input, textarea, select").forEach(function (i) { i.disabled = !present; });
        });
        Object.keys(fields).forEach(function (name) {
          var el = form.elements[name];
          if (el) el.value = fields[name] == null ? "" : fields[name];
        });
      }
    }
    dlg.showModal();
    var first = dlg.querySelector('input:not([type=hidden]):not([type=radio]):not(:disabled), textarea:not(:disabled)');
    if (first && first.offsetParent !== null) first.focus();
  }

  document.addEventListener("click", function (e) {
    var opener = e.target.closest("[data-dialog]");
    if (opener) { e.preventDefault(); openDialog(opener); return; }

    var closer = e.target.closest("[data-close]");
    if (closer) { var d = closer.closest("dialog"); if (d) d.close(); return; }

    var dismiss = e.target.closest("[data-dismiss]");
    if (dismiss) { var f = dismiss.closest(".flash"); if (f) f.remove(); }
  });

  // Click on the dark backdrop closes a dialog.
  document.querySelectorAll("dialog").forEach(function (dlg) {
    dlg.addEventListener("click", function (e) {
      if (e.target !== dlg) return;
      var r = dlg.getBoundingClientRect();
      var inside = e.clientX >= r.left && e.clientX <= r.right && e.clientY >= r.top && e.clientY <= r.bottom;
      if (!inside) dlg.close();
    });
  });

  /* ---------- Confirm before deleting ---------- */
  document.addEventListener("submit", function (e) {
    var form = e.target;
    if (form.dataset.confirm && !window.confirm(form.dataset.confirm)) {
      e.preventDefault();
    }
  });

  /* ---------- Grid / list view toggle (remembered per page type) ---------- */
  document.querySelectorAll("[data-view-for]").forEach(function (group) {
    var target = document.querySelector(group.dataset.viewFor);
    var key = "studylib-view-" + (target ? target.dataset.viewKey : group.dataset.viewFor);
    function apply(view) {
      if (target) {
        target.classList.toggle("view-grid", view === "grid");
        target.classList.toggle("view-list", view === "list");
      }
      group.querySelectorAll("[data-view]").forEach(function (b) {
        b.classList.toggle("active", b.dataset.view === view);
      });
    }
    apply(storageGet(key, "grid"));
    group.addEventListener("click", function (e) {
      var btn = e.target.closest("[data-view]");
      if (!btn) return;
      apply(btn.dataset.view);
      storageSet(key, btn.dataset.view);
    });
  });

  /* ---------- Live search + type filter ---------- */
  var filters = new Map();
  function filterState(container) {
    if (!filters.has(container)) filters.set(container, { type: "all", q: "" });
    return filters.get(container);
  }
  function applyFilter(container) {
    var st = filterState(container);
    var visible = 0;
    Array.prototype.forEach.call(container.children, function (item) {
      var okType = st.type === "all" || item.dataset.type === st.type;
      var okText = !st.q || (item.dataset.name || "").indexOf(st.q) !== -1;
      item.hidden = !(okType && okText);
      if (!item.hidden) visible++;
    });
    var msg = document.querySelector('[data-no-results="#' + container.id + '"]');
    if (msg) msg.hidden = visible > 0;
  }
  document.querySelectorAll("[data-live-search]").forEach(function (input) {
    var container = document.querySelector(input.dataset.liveSearch);
    if (!container) return;
    input.addEventListener("input", function () {
      filterState(container).q = input.value.trim().toLowerCase();
      applyFilter(container);
    });
  });
  document.querySelectorAll("[data-filter-for]").forEach(function (group) {
    var container = document.querySelector(group.dataset.filterFor);
    group.addEventListener("click", function (e) {
      var chip = e.target.closest("[data-filter]");
      if (!chip || !container) return;
      group.querySelectorAll("[data-filter]").forEach(function (c) { c.classList.toggle("active", c === chip); });
      filterState(container).type = chip.dataset.filter;
      applyFilter(container);
    });
  });

  /* ---------- Thumbnails that fail to load (e.g. offline YouTube) ---------- */
  document.querySelectorAll("img[data-fallback]").forEach(function (img) {
    function fail() { img.remove(); }
    if (img.complete && img.naturalWidth === 0) fail();
    else img.addEventListener("error", fail);
  });

  /* ---------- Add resource form ---------- */
  var addForm = document.getElementById("addResourceForm");
  if (addForm) {
    var fileInput = addForm.querySelector('input[type="file"]');
    var fileHint = addForm.querySelector("[data-file-hint]");
    var urlHint = addForm.querySelector("[data-url-hint]");
    var titleHint = addForm.querySelector("[data-title-hint]");
    var submitBtn = addForm.querySelector('button[type="submit"]');
    var URL_HINTS = {
      video: "A YouTube link or any video page / direct .mp4 link.",
      link: "Any website address. Give it a custom title below.",
      ai: "A shared ChatGPT, Gemini or other AI chat link."
    };

    var currentType = function () { return addForm.querySelector('input[name="type"]:checked'); };

    var updateForm = function () {
      var radio = currentType();
      var type = radio.value;
      addForm.querySelectorAll("[data-types]").forEach(function (section) {
        var on = section.dataset.types.split(" ").indexOf(type) !== -1;
        section.hidden = !on;
        section.querySelectorAll("input, textarea, select").forEach(function (el) { el.disabled = !on; });
      });
      if (radio.dataset.accept) {
        fileInput.accept = radio.dataset.accept;
        fileHint.textContent = "Allowed: " + radio.dataset.accept.split(",").join(", ") +
          " · up to " + radio.dataset.maxMb + " MB each. You can select several files at once.";
        titleHint.textContent = "(optional – defaults to the file name)";
      } else if (type === "note") {
        titleHint.textContent = "(optional)";
      } else {
        titleHint.textContent = "(optional – defaults to the site name)";
      }
      urlHint.textContent = URL_HINTS[type] || "";
    };

    addForm.addEventListener("change", function (e) {
      if (e.target.name === "type") { fileInput.value = ""; updateForm(); }
    });
    updateForm();

    addForm.addEventListener("submit", function (e) {
      var radio = currentType();
      if (radio.dataset.accept && fileInput.files.length) {
        var allowed = radio.dataset.accept.split(",");
        var max = Number(radio.dataset.maxMb) * 1024 * 1024;
        for (var i = 0; i < fileInput.files.length; i++) {
          var f = fileInput.files[i];
          var ext = f.name.indexOf(".") === -1 ? "" : "." + f.name.split(".").pop().toLowerCase();
          if (allowed.indexOf(ext) === -1) {
            e.preventDefault();
            alert('"' + f.name + '" is not an allowed file type.\nAllowed: ' + allowed.join(", "));
            return;
          }
          if (f.size > max) {
            e.preventDefault();
            alert('"' + f.name + '" is larger than ' + radio.dataset.maxMb + " MB.");
            return;
          }
        }
      }
      submitBtn.disabled = true;
      submitBtn.textContent = radio.dataset.accept ? "Uploading…" : "Saving…";
    });

    window.addEventListener("pageshow", function () {
      submitBtn.disabled = false;
      submitBtn.textContent = "Add resource";
    });
  }

  /* ---------- Note editor toggle ---------- */
  var noteView = document.getElementById("noteView");
  var noteEditor = document.getElementById("noteEditor");
  if (noteView && noteEditor) {
    document.querySelectorAll("[data-toggle-note]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        var editing = noteEditor.hidden;
        noteEditor.hidden = !editing;
        noteView.hidden = editing;
        if (editing) noteEditor.querySelector("textarea[name=content]").focus();
      });
    });
  }

  /* ---------- Drag & drop unit reordering ---------- */
  var unitList = document.getElementById("unitList");
  if (unitList) {
    var dragged = null;
    var orderOf = function () {
      return Array.prototype.map.call(unitList.querySelectorAll(".unit-row"), function (r) { return Number(r.dataset.id); });
    };
    var startOrder = orderOf().join(",");

    unitList.addEventListener("dragstart", function (e) {
      var row = e.target.closest(".unit-row");
      if (!row) return;
      dragged = row;
      row.classList.add("dragging");
      e.dataTransfer.effectAllowed = "move";
      e.dataTransfer.setData("text/plain", row.dataset.id);
    });
    unitList.addEventListener("dragover", function (e) {
      if (!dragged) return;
      e.preventDefault();
      var row = e.target.closest(".unit-row");
      if (!row || row === dragged) return;
      var rect = row.getBoundingClientRect();
      var after = e.clientY > rect.top + rect.height / 2;
      unitList.insertBefore(dragged, after ? row.nextSibling : row);
    });
    unitList.addEventListener("drop", function (e) { if (dragged) e.preventDefault(); });
    unitList.addEventListener("dragend", function () {
      if (!dragged) return;
      dragged.classList.remove("dragging");
      dragged = null;
      var order = orderOf();
      if (order.join(",") === startOrder) return;
      fetch(unitList.dataset.reorderUrl, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRFToken": CSRF },
        body: JSON.stringify({ order: order })
      })
        .then(function (res) { return res.json().catch(function () { return { ok: false, error: "Server error" }; }); })
        .then(function (data) { if (!data.ok) alert("Could not save the new order: " + (data.error || "unknown error")); })
        .catch(function () { alert("Could not save the new order. Is the server running?"); })
        .then(function () { window.location.reload(); });
    });
  }
})();

