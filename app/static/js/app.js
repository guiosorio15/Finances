/* raiz — small progressive enhancements. No frameworks. */
(() => {
  "use strict";

  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

  const EUR = new Intl.NumberFormat("pt-PT", {
    style: "currency",
    currency: "EUR",
    minimumFractionDigits: 2,
    maximumFractionDigits: 2,
  });

  const EUR0 = new Intl.NumberFormat("pt-PT", {
    style: "currency",
    currency: "EUR",
    minimumFractionDigits: 0,
    maximumFractionDigits: 0,
  });

  function parseMoney(value) {
    if (value === null || value === undefined) return 0;
    let raw = String(value).replace(/[€\s\u00a0]/g, "").replace("−", "-");
    if (!raw) return 0;
    const negative = raw.startsWith("-");
    raw = raw.replace(/^[+-]/, "");
    if (raw.includes(",") && raw.includes(".")) {
      raw = raw.replace(/\./g, "").replace(",", ".");
    } else if (raw.includes(",")) {
      raw = raw.replace(",", ".");
    } else if (/^\d{1,3}(\.\d{3})+$/.test(raw)) {
      raw = raw.replace(/\./g, "");
    }
    const number = Number.parseFloat(raw);
    return Number.isFinite(number) ? (negative ? -number : number) : 0;
  }

  function formatMoney(value) {
    return EUR.format(Number(value) || 0);
  }

  function inputMoney(value) {
    const number = Math.abs(Number(value) || 0);
    return number.toFixed(2).replace(".", ",");
  }

  /* ------------------------------------------------------------------------
     Navigation
  ------------------------------------------------------------------------ */

  const openNav = () => document.body.classList.add("nav-open");
  const closeNav = () => document.body.classList.remove("nav-open");

  $$("[data-nav-open]").forEach((button) => button.addEventListener("click", openNav));
  $$("[data-nav-close]").forEach((button) => button.addEventListener("click", closeNav));

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeNav();
  });

  /* ------------------------------------------------------------------------
     Toast
  ------------------------------------------------------------------------ */

  const toast = $("[data-toast]");
  if (toast) {
    const dismiss = () => {
      toast.classList.add("is-leaving");
      window.setTimeout(() => toast.remove(), 220);
    };
    $("[data-toast-close]", toast)?.addEventListener("click", dismiss);
    window.setTimeout(dismiss, 5600);
  }

  /* ------------------------------------------------------------------------
     Dialogs
  ------------------------------------------------------------------------ */

  function openModal(dialog) {
    if (!dialog) return;
    if (!dialog.open) dialog.showModal();
    document.body.classList.add("modal-open");
    const first = dialog.querySelector(
      "input:not([type=hidden]):not([type=file]), select, textarea"
    );
    if (first) window.setTimeout(() => first.focus(), 60);
  }

  $$("dialog").forEach((dialog) => {
    dialog.addEventListener("close", () => {
      document.body.classList.remove("modal-open");
    });
    dialog.addEventListener("click", (event) => {
      if (event.target === dialog) dialog.close();
    });
  });

  document.addEventListener("click", (event) => {
    const closer = event.target.closest("[data-close-modal]");
    if (closer) {
      closer.closest("dialog")?.close();
      return;
    }

    if (event.target.closest("[data-open-transaction]")) {
      openTransactionModal();
      return;
    }
    if (event.target.closest("[data-open-subscription]")) {
      openSubscriptionModal();
      return;
    }
    if (event.target.closest("[data-open-goal]")) {
      openGoalModal();
      return;
    }
    if (event.target.closest("[data-open-account]")) {
      openModal($("#account-modal"));
      return;
    }
    if (event.target.closest("[data-open-category]")) {
      openModal($("#category-modal"));
      return;
    }

    const editTransaction = event.target.closest("[data-edit-transaction]");
    if (editTransaction) {
      openTransactionModal(JSON.parse(editTransaction.dataset.editTransaction));
      return;
    }

    const editSubscription = event.target.closest("[data-edit-subscription]");
    if (editSubscription) {
      openSubscriptionModal(JSON.parse(editSubscription.dataset.editSubscription));
      return;
    }

    const editGoal = event.target.closest("[data-edit-goal]");
    if (editGoal) {
      openGoalModal(JSON.parse(editGoal.dataset.editGoal));
    }
  });

  /* ------------------------------------------------------------------------
     Transactions
  ------------------------------------------------------------------------ */

  const transactionModal = $("#transaction-modal");
  const transactionForm = $("#transaction-form");

  function filterTransactionCategories(type) {
    const select = $("#tx-category");
    if (!select) return;
    let firstVisible = null;
    $$("optgroup", select).forEach((group) => {
      let visible = 0;
      $$("option", group).forEach((option) => {
        const kind = option.dataset.kind || "expense";
        const show = type === "income" ? kind === "income" : kind !== "income";
        option.hidden = !show;
        option.disabled = !show;
        if (show) {
          visible += 1;
          if (!firstVisible) firstVisible = option;
        }
      });
      group.hidden = visible === 0;
    });
    const selected = select.selectedOptions[0];
    if ((!selected || selected.hidden || selected.disabled) && firstVisible) {
      select.value = firstVisible.value;
    }
  }

  function openTransactionModal(transaction = null) {
    if (!transactionForm || !transactionModal) return;
    const isEdit = Boolean(transaction && transaction.id);
    transactionForm.reset();
    transactionForm.action = isEdit ? `/transactions/${transaction.id}` : "/transactions";
    $("#transaction-modal [data-modal-eyebrow]").textContent = isEdit
      ? "Movimento existente"
      : "Novo movimento";
    $("#transaction-modal [data-modal-title]").textContent = isEdit
      ? "Editar movimento"
      : "Adicionar movimento";

    const type = transaction?.type || "expense";
    transactionForm.elements.type.value = type;
    filterTransactionCategories(type);
    transactionForm.elements.date.value = transaction?.date || transactionForm.dataset.today || new Date().toISOString().slice(0, 10);

    if (isEdit) {
      transactionForm.elements.description.value = transaction.description || "";
      transactionForm.elements.amount.value = inputMoney(transaction.amount);
      transactionForm.elements.notes.value = transaction.notes || "";
      if (transaction.account_id) transactionForm.elements.account_id.value = transaction.account_id;
      filterTransactionCategories(type);
      if (transaction.category_id) transactionForm.elements.category_id.value = transaction.category_id;
    } else {
      transactionForm.elements.amount.value = "";
      transactionForm.elements.description.value = "";
      transactionForm.elements.notes.value = "";
      if (!transactionForm.elements.account_id.value) {
        const first = transactionForm.elements.account_id.options[0];
        if (first) transactionForm.elements.account_id.value = first.value;
      }
    }
    openModal(transactionModal);
  }

  $("#tx-type")?.addEventListener("change", (event) => {
    filterTransactionCategories(event.target.value);
  });

  if (transactionForm && !transactionForm.dataset.today) {
    transactionForm.dataset.today = new Date().toISOString().slice(0, 10);
  }

  /* ------------------------------------------------------------------------
     Subscriptions
  ------------------------------------------------------------------------ */

  const subscriptionModal = $("#subscription-modal");
  const subscriptionForm = $("#subscription-form");

  function openSubscriptionModal(subscription = null) {
    if (!subscriptionForm || !subscriptionModal) return;
    const isEdit = Boolean(subscription && subscription.id);
    subscriptionForm.reset();
    subscriptionForm.action = isEdit ? `/subscriptions/${subscription.id}` : "/subscriptions";
    $("#subscription-modal [data-modal-eyebrow]").textContent = isEdit
      ? "Subscrição existente"
      : "Nova subscrição";
    $("#subscription-modal [data-modal-title]").textContent = isEdit
      ? "Editar subscrição"
      : "Adicionar subscrição";

    if (isEdit) {
      subscriptionForm.elements.name.value = subscription.name || "";
      subscriptionForm.elements.amount.value = inputMoney(subscription.amount);
      subscriptionForm.elements.cadence.value = subscription.cadence || "monthly";
      subscriptionForm.elements.next_charge.value = subscription.next_charge || "";
      if (subscription.category_id) subscriptionForm.elements.category_id.value = subscription.category_id;
      if (subscription.account_id) subscriptionForm.elements.account_id.value = subscription.account_id;
      subscriptionForm.elements.url.value = subscription.url || "";
      subscriptionForm.elements.notes.value = subscription.notes || "";
    } else {
      subscriptionForm.elements.next_charge.value = new Date().toISOString().slice(0, 10);
    }
    openModal(subscriptionModal);
  }

  /* ------------------------------------------------------------------------
     Goals
  ------------------------------------------------------------------------ */

  const goalModal = $("#goal-modal");
  const goalForm = $("#goal-form");

  function openGoalModal(goal = null) {
    if (!goalForm || !goalModal) return;
    const isEdit = Boolean(goal && goal.id);
    goalForm.reset();
    goalForm.action = isEdit ? `/goals/${goal.id}` : "/goals";
    $("#goal-modal [data-modal-eyebrow]").textContent = isEdit ? "Objetivo existente" : "Novo objetivo";
    $("#goal-modal [data-modal-title]").textContent = isEdit ? "Editar objetivo" : "Criar objetivo";

    if (isEdit) {
      goalForm.elements.name.value = goal.name || "";
      goalForm.elements.kind.value = goal.kind || "saving";
      goalForm.elements.deadline.value = goal.deadline || "";
      goalForm.elements.target.value = inputMoney(goal.target);
      goalForm.elements.current.value = inputMoney(goal.current);
      goalForm.elements.monthly_contribution.value = inputMoney(goal.monthly_contribution);
      goalForm.elements.color.value = goal.color || "#2F5D46";
      goalForm.elements.notes.value = goal.notes || "";
    }
    openModal(goalModal);
  }

  /* ------------------------------------------------------------------------
     Upload
  ------------------------------------------------------------------------ */

  const dropzone = $("[data-dropzone]");
  if (dropzone) {
    const input = $("input[type=file]", dropzone);
    const form = $("#upload-form");
    const submit = $("[data-upload-submit]", form);
    const fileName = $("[data-file-name]", dropzone);

    const updateFile = () => {
      const file = input.files?.[0];
      if (!file) {
        fileName.textContent = "";
        submit.disabled = true;
        return;
      }
      const isPdf = file.type === "application/pdf" || file.name.toLowerCase().endsWith(".pdf");
      fileName.textContent = isPdf ? file.name : `${file.name} — escolhe um PDF.`;
      fileName.style.color = isPdf ? "" : "var(--terracotta)";
      submit.disabled = !isPdf;
    };

    input.addEventListener("change", updateFile);
    dropzone.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        input.click();
      }
    });
    ["dragenter", "dragover"].forEach((name) => {
      dropzone.addEventListener(name, (event) => {
        event.preventDefault();
        dropzone.classList.add("is-dragover");
      });
    });
    ["dragleave", "drop"].forEach((name) => {
      dropzone.addEventListener(name, (event) => {
        event.preventDefault();
        dropzone.classList.remove("is-dragover");
      });
    });
    dropzone.addEventListener("drop", (event) => {
      if (event.dataTransfer?.files?.length) {
        input.files = event.dataTransfer.files;
        updateFile();
      }
    });
    form.addEventListener("submit", () => {
      if (!submit.disabled) {
        submit.disabled = true;
        submit.textContent = "A ler o recibo…";
      }
    });
  }

  /* ------------------------------------------------------------------------
     Payslip review live summary
  ------------------------------------------------------------------------ */

  const review = $("[data-payslip-review]");
  if (review) {
    const form = $("#review-form");
    const field = (name) => form?.elements?.[name] || null;
    const value = (name) => parseMoney(field(name)?.value || 0);

    const setText = (key, text) => {
      $$(`[data-summary="${key}"]`, review).forEach((node) => {
        node.textContent = text;
      });
    };

    const setBar = (key, percent) => {
      $$(`[data-bar="${key}"]`, review).forEach((node) => {
        node.style.width = `${Math.max(0, Math.min(100, percent))}%`;
      });
    };

    const update = () => {
      const base = value("base_salary");
      const christmas = value("christmas_subsidy");
      const holiday = value("holiday_subsidy");
      const otherEarnings = value("other_earnings");
      const irs = value("irs_amount");
      const ss = value("ss_amount");
      const otherDeductions = value("other_deductions");
      const mealDays = value("meal_days");
      const mealDaily = value("meal_daily");
      const mealAllowance = value("meal_allowance");

      const grossTaxable = base + christmas + holiday + otherEarnings;
      const deductions = irs + ss + otherDeductions;
      const calculatedNet = Math.max(0, grossTaxable - deductions);
      const meal = mealDays > 0 && mealDaily > 0 ? mealDays * mealDaily : mealAllowance;
      const total = calculatedNet + meal;
      const denominator = grossTaxable + meal;
      const rate = grossTaxable > 0 ? (deductions / grossTaxable) * 100 : 0;

      setText("gross-taxable", formatMoney(grossTaxable));
      setText("irs", formatMoney(irs));
      setText("ss", formatMoney(ss));
      setText("net-cash", formatMoney(calculatedNet));
      setText("meal", formatMoney(meal));
      setText("total", formatMoney(total));
      setText("total-2", formatMoney(total));
      setText("rate", `${rate.toFixed(1).replace(".", ",")}%`);

      if (denominator > 0) {
        setBar("net", (calculatedNet / denominator) * 100);
        setBar("irs", (irs / denominator) * 100);
        setBar("ss", (ss / denominator) * 100);
        setBar("meal", (meal / denominator) * 100);
      }

      const cashInput = value("net_cash");
      const totalInput = value("total_net");
      const mismatch = $("[data-review-mismatch]", review);
      if (mismatch) {
        const problems = [];
        if (cashInput > 0 && Math.abs(cashInput - calculatedNet) > 0.02) {
          problems.push(
            `o líquido em conta está ${formatMoney(cashInput)} mas os campos dão ${formatMoney(calculatedNet)}`
          );
        }
        if (totalInput > 0 && Math.abs(totalInput - total) > 0.02) {
          problems.push(
            `o total está ${formatMoney(totalInput)} mas os campos dão ${formatMoney(total)}`
          );
        }
        mismatch.hidden = problems.length === 0;
        mismatch.textContent = problems.length
          ? `Confirma: ${problems.join("; ")}.`
          : "";
      }
    };

    form?.addEventListener("input", update);
    update();
  }

  /* ------------------------------------------------------------------------
     Allocation total
  ------------------------------------------------------------------------ */

  const allocationForm = $("[data-allocation-form]");
  if (allocationForm) {
    const inputs = $$("[data-alloc-input]", allocationForm);
    const output = $("[data-allocation-sum]", allocationForm);
    const update = () => {
      const total = inputs.reduce((sum, input) => sum + (Number.parseInt(input.value, 10) || 0), 0);
      output.textContent = `Total: ${total}%`;
      output.classList.toggle("is-error", total !== 100);
    };
    inputs.forEach((input) => input.addEventListener("input", update));
    update();
  }

  /* ------------------------------------------------------------------------
     Month menu: close on outside click
  ------------------------------------------------------------------------ */

  document.addEventListener("click", (event) => {
    $$("details.month-menu[open]").forEach((details) => {
      if (!details.contains(event.target)) details.removeAttribute("open");
    });
  });
})();
