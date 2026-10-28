(function () {
  "use strict";

  var button = document.querySelector("[data-back-to-top]");
  if (!button) { return; }

  var isVisible = false;
  function updateVisibility() {
    var scrollTop = window.scrollY || window.pageYOffset || 0;
    var shouldShow = scrollTop > 320;
    if (shouldShow === isVisible) { return; }
    isVisible = shouldShow;
    button.hidden = !shouldShow;
  }

  button.addEventListener("click", function () {
    var reduceMotion = window.matchMedia && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    var main = document.getElementById("main-content");
    if (main) { main.focus({ preventScroll: true }); }
    window.scrollTo({ top: 0, left: 0, behavior: reduceMotion ? "auto" : "smooth" });
  });

  window.addEventListener("scroll", updateVisibility, { passive: true });
  window.addEventListener("pageshow", updateVisibility);
  updateVisibility();
})();
