(function () {
  function submitAction(form, submitter) {
    if (submitter && submitter.getAttribute("formaction")) {
      return submitter.getAttribute("formaction");
    }
    return form.getAttribute("action") || window.location.pathname;
  }

  function setBusy(form, submitter, isRun) {
    form.dataset.submitting = "true";
    form.classList.add("is-submitting");
    form.setAttribute("aria-busy", "true");

    form.querySelectorAll('button[type="submit"]').forEach(function (button) {
      button.disabled = true;
      button.setAttribute("aria-disabled", "true");
    });

    if (submitter) {
      submitter.dataset.originalLabel = submitter.textContent;
      submitter.textContent = submitter.getAttribute("data-running-label") || (isRun ? "실행 중..." : "확인 중...");
      submitter.classList.add("is-active-submit");
    }

    var status = form.querySelector("[data-script-running-status]");
    if (status) {
      status.hidden = !isRun;
      if (isRun) {
        status.textContent = "스크립트를 실행 중입니다. 완료될 때까지 기다려 주세요.";
      }
    }
  }

  document.addEventListener("submit", function (event) {
    var form = event.target;
    if (!form.classList || !form.classList.contains("script-exec-form")) {
      return;
    }

    if (form.dataset.submitting === "true") {
      event.preventDefault();
      return;
    }

    var submitter = event.submitter || document.activeElement;
    var action = submitAction(form, submitter);
    var isRun = action.indexOf("/scripts/run") !== -1;
    var isPreview = action.indexOf("/scripts/preview") !== -1;
    if (!isRun && !isPreview) {
      return;
    }

    setBusy(form, submitter, isRun);
  });
}());