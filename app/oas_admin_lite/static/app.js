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

  document.addEventListener("input", function (event) {
    var form = event.target.closest && event.target.closest(".script-exec-form");
    if (!form) {
      return;
    }
    var runButton = form.querySelector('button[formaction="/scripts/run"]');
    if (runButton) {
      runButton.disabled = true;
      runButton.setAttribute("aria-disabled", "true");
      runButton.setAttribute("title", "명령어 미리보기를 다시 수행하세요.");
    }
  });

  document.addEventListener("click", function (event) {
    var button = event.target.closest && event.target.closest("[data-metric-advice]");
    if (!button) {
      return;
    }
    var advice = button.nextElementSibling;
    if (!advice) {
      return;
    }
    var isOpen = button.getAttribute("aria-expanded") === "true";
    button.setAttribute("aria-expanded", String(!isOpen));
    advice.hidden = isOpen;
  });

  document.addEventListener("click", function (event) {
    var openButton = event.target.closest && event.target.closest("[data-report-example-open]");
    if (openButton) {
      var dialog = openButton.closest(".script-exec-form").querySelector("[data-report-example-dialog]");
      if (dialog && dialog.showModal) {
        dialog.showModal();
      }
      return;
    }

    var closeButton = event.target.closest && event.target.closest("[data-report-example-close]");
    if (closeButton) {
      var closeDialog = closeButton.closest("dialog");
      if (closeDialog) {
        closeDialog.close();
      }
      return;
    }

    var example = event.target.closest && event.target.closest("[data-report-example]");
    if (!example) {
      return;
    }
    var form = example.closest(".script-exec-form");
    if (!form) {
      return;
    }
    var fields = {
      report_output: "reportOutput",
      report_type: "reportType",
      report_fields: "reportFields",
      report_folder: "reportFolder"
    };
    Object.keys(fields).forEach(function (name) {
      var input = form.elements[name];
      var value = example.dataset[fields[name]];
      if (input && value !== undefined) {
        input.value = value;
        input.dispatchEvent(new Event("input", { bubbles: true }));
      }
    });
    var exampleDialog = example.closest("dialog");
    if (exampleDialog) {
      exampleDialog.close();
    }
  });

  document.querySelectorAll(".script-form-grid").forEach(function (grid) {
    var options = grid.querySelectorAll(".checkbox-field input[name^='report_']");
    if (!options.length || options[0].closest(".report-options")) {
      return;
    }
    var group = document.createElement("div");
    group.className = "report-options full";
    options[0].closest("label").before(group);
    options.forEach(function (input) {
      group.appendChild(input.closest("label"));
    });
  });

  document.querySelectorAll(".command-preview textarea").forEach(function (textarea) {
    textarea.style.height = "auto";
    textarea.style.height = textarea.scrollHeight + "px";
  });
}());
