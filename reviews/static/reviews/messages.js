(function () {
  "use strict";
  document.addEventListener("click", function (event) {
    var button = event.target.closest(".message-close");
    if (!button) { return; }
    var message = button.closest(".message");
    if (message) { message.remove(); }
  });
})();
