(function () {
  "use strict";

  var BUSY_CLASS = "is-loading";
  var BUSY_ATTR = "submitState";

  function findSubmitter(form, event) {
    if (event.submitter && form.contains(event.submitter)) { return event.submitter; }
    return form.querySelector('button[type="submit"], button:not([type])');
  }

  function blockSubmitter(button) {
    if (!button || button.tagName !== "BUTTON" || button.disabled) { return; }
    button.classList.add(BUSY_CLASS);
    button.setAttribute("aria-busy", "true");
    if (button.name) {
      // Keep named buttons enabled so their name/value is included in the POST.
      button.setAttribute("aria-disabled", "true");
    } else {
      button.disabled = true;
    }
  }

  function resetForm(form) {
    delete form.dataset[BUSY_ATTR];
    Array.prototype.forEach.call(
      form.querySelectorAll("." + BUSY_CLASS),
      function (button) {
        button.disabled = false;
        button.classList.remove(BUSY_CLASS);
        button.removeAttribute("aria-busy");
        button.removeAttribute("aria-disabled");
      }
    );
  }

  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!(form instanceof HTMLFormElement) || event.defaultPrevented) { return; }
    if (form.dataset[BUSY_ATTR] === "true") { event.preventDefault(); return; }
    form.dataset[BUSY_ATTR] = "true";

    blockSubmitter(findSubmitter(form, event));
  });

  document.addEventListener("reset", function (event) {
    if (event.target instanceof HTMLFormElement) { resetForm(event.target); }
  });

  window.addEventListener("pageshow", function (event) {
    if (!event.persisted) { return; }
    Array.prototype.forEach.call(document.forms, resetForm);
  });
})();
