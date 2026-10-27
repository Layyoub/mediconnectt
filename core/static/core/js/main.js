// MediConnect — small UI helpers (no framework needed)
document.addEventListener("DOMContentLoaded", () => {
  // Public navbar toggle (mobile)
  const navToggle = document.querySelector("[data-nav-toggle]");
  const navMenu = document.querySelector("[data-nav-menu]");
  if (navToggle && navMenu) {
    navToggle.addEventListener("click", () => {
      const open = navMenu.classList.toggle("is-open");
      navToggle.setAttribute("aria-expanded", open);
      navToggle.querySelector("i").className = open ? "bi bi-x-lg" : "bi bi-list";
    });
    navMenu.querySelectorAll("a").forEach((link) =>
      link.addEventListener("click", () => {
        navMenu.classList.remove("is-open");
        navToggle.setAttribute("aria-expanded", "false");
        navToggle.querySelector("i").className = "bi bi-list";
      })
    );
  }

  // Dashboard sidebar toggle (tablet / mobile)
  const sidebar = document.querySelector("[data-sidebar]");
  const backdrop = document.querySelector("[data-sidebar-backdrop]");
  const setSidebar = (open) => {
    sidebar?.classList.toggle("is-open", open);
    backdrop?.classList.toggle("is-open", open);
  };
  document.querySelectorAll("[data-sidebar-toggle]").forEach((btn) =>
    btn.addEventListener("click", () => setSidebar(!sidebar.classList.contains("is-open")))
  );
  backdrop?.addEventListener("click", () => setSidebar(false));

  // Show / hide password
  document.querySelectorAll("[data-password-toggle]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const input = btn.parentElement.querySelector("input");
      const show = input.type === "password";
      input.type = show ? "text" : "password";
      btn.querySelector("i").className = show ? "bi bi-eye-slash" : "bi bi-eye";
      btn.setAttribute("aria-label", show ? "Masquer le mot de passe" : "Afficher le mot de passe");
    });
  });

  // File upload: show the selected file name
  document.querySelectorAll(".file-drop").forEach((zone) => {
    const input = zone.querySelector("input[type=file]");
    const label = zone.querySelector(".file-name");
    if (!input) return;
    const defaultText = label.textContent;
    input.addEventListener("change", () => {
      const file = input.files[0];
      zone.classList.toggle("has-file", Boolean(file));
      label.textContent = file ? `${file.name} · ${(file.size / 1024 / 1024).toFixed(2)} Mo` : defaultText;
    });
    ["dragenter", "dragover"].forEach((e) => zone.addEventListener(e, () => zone.classList.add("is-dragover")));
    ["dragleave", "drop"].forEach((e) => zone.addEventListener(e, () => zone.classList.remove("is-dragover")));
  });

  // Doctor decision: show the reason field only when rejecting
  const decision = document.querySelectorAll("input[name=decision]");
  const reasonBlock = document.querySelector("[data-reason-block]");
  if (decision.length && reasonBlock) {
    const sync = () => {
      const checked = document.querySelector("input[name=decision]:checked");
      reasonBlock.hidden = !checked || checked.value !== "reject";
    };
    decision.forEach((r) => r.addEventListener("change", sync));
    sync();
  }

  // Triage: expand a symptom card when it is checked, and count selections
  const symptomCards = document.querySelectorAll("[data-symptom]");
  const counter = document.querySelector("[data-symptom-count]");
  const updateCount = () => {
    if (!counter) return;
    const n = document.querySelectorAll("[data-symptom-check]:checked").length;
    counter.textContent = n ? `${n} symptôme${n > 1 ? "s" : ""} sélectionné${n > 1 ? "s" : ""}` : "Aucun symptôme sélectionné";
  };
  symptomCards.forEach((card) => {
    const check = card.querySelector("[data-symptom-check]");
    check.addEventListener("change", () => {
      card.classList.toggle("is-checked", check.checked);
      updateCount();
    });
  });
  updateCount();

  // Forms that take a few seconds (AI analysis): disable the button and show progress
  // Print buttons (consultation report)
  document.querySelectorAll("[data-print]").forEach((btn) => btn.addEventListener("click", () => window.print()));

  // Ask for confirmation before following a link that starts something
  document.querySelectorAll("a[data-confirm-link]").forEach((link) =>
    link.addEventListener("click", (e) => {
      if (!window.confirm(link.dataset.confirmLink)) e.preventDefault();
    })
  );

  // Ask for confirmation before submitting sensitive forms
  document.querySelectorAll("form[data-confirm]").forEach((form) =>
    form.addEventListener("submit", (e) => {
      if (!window.confirm(form.dataset.confirm)) e.preventDefault();
    })
  );

  // Appointments: show the chosen slot in the summary box
  const slotSummary = document.querySelector("[data-slot-summary]");
  if (slotSummary) {
    const showSlot = () => {
      const checked = document.querySelector("input[name=slot]:checked");
      if (!checked) return;
      const strong = document.createElement("strong");
      strong.textContent = checked.dataset.slotLabel;
      slotSummary.replaceChildren(strong);
    };
    document.querySelectorAll("input[name=slot]").forEach((r) => r.addEventListener("change", showSlot));
    showSlot();
  }

  // Prescription: add / remove drug rows (Django formset)
  document.querySelectorAll("[data-formset]").forEach((box) => {
    const prefix = box.dataset.prefix;
    const form = box.closest("form");
    const rows = box.querySelector("[data-formset-rows]");
    const template = box.querySelector("template[data-formset-empty]");
    const addBtn = box.querySelector("[data-formset-add]");
    const total = form.querySelector(`input[name="${prefix}-TOTAL_FORMS"]`);
    const max = parseInt(form.querySelector(`input[name="${prefix}-MAX_NUM_FORMS"]`).value, 10);
    const pattern = new RegExp(`^(id_)?${prefix}-(\\d+|__prefix__)-`);
    const renumber = () => {
      const list = rows.querySelectorAll("[data-formset-row]");
      list.forEach((row, i) => {
        row.querySelectorAll("[name], [id], label[for]").forEach((el) => {
          ["name", "id", "for"].forEach((attr) => {
            const value = el.getAttribute(attr);
            if (value) el.setAttribute(attr, value.replace(pattern, (_, id) => `${id || ""}${prefix}-${i}-`));
          });
        });
        row.querySelector("[data-formset-num]").textContent = i + 1;
        row.querySelector("[data-formset-remove]").hidden = list.length === 1;
      });
      total.value = list.length;
      addBtn.hidden = list.length >= max;
    };
    addBtn.addEventListener("click", () => {
      rows.appendChild(template.content.cloneNode(true));
      renumber();
      rows.lastElementChild.querySelector("input:not([type=hidden])")?.focus();
    });
    rows.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-formset-remove]");
      if (!btn) return;
      btn.closest("[data-formset-row]").remove();
      renumber();
    });
    renumber();
  });

  // Back-office: refresh while the doctor verification is running
  if (document.querySelector("[data-reload-while-running]")) {
    setTimeout(() => window.location.reload(), 4000);
  }

  document.querySelectorAll("[data-loading-form]").forEach((form) => {
    form.addEventListener("submit", () => {
      const btn = form.querySelector("[data-loading-text]");
      if (!btn) return;
      btn.disabled = true;
      btn.innerHTML = `<span class="spinner" aria-hidden="true"></span> ${btn.dataset.loadingText}`;
    });
  });
});

// --------------------------------------------------------------------------
// Accessibility: text size, theme (light / dark / system), high contrast,
// keyboard shortcuts. Settings live in this browser (localStorage) and are
// applied by the inline script in base.html before the page is drawn.
// --------------------------------------------------------------------------
(() => {
  const KEY = "mc-a11y";
  const root = document.documentElement;
  const darkQuery = window.matchMedia("(prefers-color-scheme: dark)");

  const load = () => {
    try { return JSON.parse(localStorage.getItem(KEY) || "{}"); } catch (e) { return {}; }
  };
  const save = (prefs) => {
    try { localStorage.setItem(KEY, JSON.stringify(prefs)); } catch (e) { /* private mode: applies to this page only */ }
  };
  let prefs = load();

  function apply() {
    if (prefs.size) root.setAttribute("data-text-size", prefs.size);
    else root.removeAttribute("data-text-size");
    const theme = prefs.theme || "light";
    root.setAttribute("data-theme", theme === "system" ? (darkQuery.matches ? "dark" : "light") : theme);
    if (prefs.contrast) root.setAttribute("data-contrast", "high");
    else root.removeAttribute("data-contrast");
  }
  darkQuery.addEventListener?.("change", () => { if (prefs.theme === "system") apply(); });

  const dialog = document.querySelector("[data-a11y-dialog]");

  function syncControls() {
    if (!dialog) return;
    dialog.querySelectorAll("input[name=a11y-size]").forEach((i) => { i.checked = i.value === (prefs.size || ""); });
    dialog.querySelectorAll("input[name=a11y-theme]").forEach((i) => { i.checked = i.value === (prefs.theme || "light"); });
    dialog.querySelector("input[name=a11y-contrast]").checked = Boolean(prefs.contrast);
  }

  function openPanel(focusShortcuts) {
    if (!dialog) return;
    syncControls();
    if (!dialog.open) dialog.showModal();
    if (focusShortcuts) dialog.querySelector("[data-a11y-shortcuts]").scrollIntoView({ block: "start" });
  }

  if (dialog) {
    dialog.addEventListener("change", (e) => {
      const input = e.target;
      if (input.name === "a11y-size") prefs.size = input.value || undefined;
      else if (input.name === "a11y-theme") prefs.theme = input.value;
      else if (input.name === "a11y-contrast") prefs.contrast = input.checked || undefined;
      else return;
      save(prefs);
      apply();
    });
    dialog.querySelector("[data-a11y-reset]").addEventListener("click", () => {
      prefs = {};
      save(prefs);
      apply();
      syncControls();
    });
    dialog.querySelectorAll("[data-a11y-close]").forEach((b) => b.addEventListener("click", () => dialog.close()));
    dialog.addEventListener("click", (e) => { if (e.target === dialog) dialog.close(); });
  }
  document.querySelectorAll("[data-a11y-open]").forEach((b) => b.addEventListener("click", () => openPanel(false)));

  // Skip link: move the focus into the main content
  document.querySelector(".skip-link")?.addEventListener("click", () => {
    setTimeout(() => document.getElementById("contenu")?.focus(), 0);
  });

  // Keyboard shortcuts: Alt + Shift + letter (physical key, so AZERTY works too)
  let links = [];
  try { links = JSON.parse(document.getElementById("a11y-shortcuts")?.textContent || "[]"); } catch (e) { /* none */ }
  const pages = Object.fromEntries(links.map((s) => [`Key${s.key}`, s.url]));

  const clickIfVisible = (selector) => {
    const el = document.querySelector(selector);
    if (el && !el.hidden && !el.disabled) { el.click(); return true; }
    return false;
  };
  const roomActions = {
    KeyM: () => clickIfVisible("[data-toggle-mic]"),
    KeyV: () => clickIfVisible("[data-toggle-cam]"),
    KeyT: () => {
      const field = document.querySelector("[data-chat-form] textarea");
      if (field && !field.disabled) { field.focus(); return true; }
      return false;
    },
  };

  const typing = (el) => el && (el.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName));

  document.addEventListener("keydown", (e) => {
    if (e.altKey && e.shiftKey && !e.ctrlKey && !e.metaKey) {
      if (e.code === "KeyA") { e.preventDefault(); openPanel(false); return; }
      if (roomActions[e.code] && document.querySelector("[data-room]")) {
        if (roomActions[e.code]()) e.preventDefault();
        return;
      }
      if (pages[e.code]) { e.preventDefault(); window.location.href = pages[e.code]; }
      return;
    }
    if (e.key === "?" && !e.ctrlKey && !e.metaKey && !typing(document.activeElement) && !document.querySelector("dialog[open]")) {
      e.preventDefault();
      openPanel(true);
    }
  });
})();
