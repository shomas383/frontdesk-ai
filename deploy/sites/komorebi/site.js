// The site's own 「質問する」 / 「AI受付に質問」 buttons open the chat widget (widget.js, loaded async).
"use strict";
for (const button of document.querySelectorAll(".js-ask")) {
  button.addEventListener("click", () => {
    const widget = window.FrontdeskWidget;
    if (widget && widget.open) widget.open();
  });
}
