/* The AI Tech Stack Planner - the web front end.
 *
 * Plain vanilla JavaScript: no framework, no build step and no CDN, so the
 * page keeps working offline.  Everything the page knows about the goals
 * and the constraints comes from GET /api/options, so nothing is hard coded
 * here.  Every error is shown in the page - the console is never the only
 * place a problem shows up.
 */
(function () {
  "use strict";

  var els = {
    form: document.getElementById("plan-form"),
    goal: document.getElementById("goal"),
    budget: document.getElementById("budget"),
    weeks: document.getElementById("timeline_weeks"),
    constraints: document.getElementById("constraints"),
    useLlm: document.getElementById("use_llm"),
    usePricing: document.getElementById("use_pricing"),
    saveAs: document.getElementById("save_as"),
    submit: document.getElementById("plan-button"),
    resultsPanel: document.getElementById("results-panel"),
    results: document.getElementById("results"),
    plans: document.getElementById("plans"),
    error: document.getElementById("error")
  };

  /* What GET /api/options told us. */
  var options = { goals: {}, constraints: {}, defaults: {} };

  /* The name of the saved plan that is open, or null for a fresh plan. */
  var openPlan = null;

  /* --- Small helpers ----------------------------------------------------- */

  function el(tag, className, text) {
    var node = document.createElement(tag);
    if (className) {
      node.className = className;
    }
    if (text !== undefined && text !== null) {
      node.textContent = String(text);
    }
    return node;
  }

  function clear(node) {
    while (node && node.firstChild) {
      node.removeChild(node.firstChild);
    }
  }

  function showError(message) {
    els.error.textContent = message;
    els.error.hidden = false;
  }

  function clearError() {
    els.error.textContent = "";
    els.error.hidden = true;
  }

  /* The same wording the command line uses. */
  function formatAmount(amount) {
    var value = Number(amount);
    if (!isFinite(value)) {
      value = 0;
    }
    var rounded = Math.round(value * 100) / 100;
    if (rounded === Math.round(rounded)) {
      return "$" + Math.round(rounded);
    }
    return "$" + rounded.toFixed(2);
  }

  function formatCost(amount) {
    return formatAmount(amount) + "/mo";
  }

  function budgetVerdict(monthlyCost, budget) {
    if (monthlyCost <= budget) {
      return "under budget";
    }
    return "over budget — trim or phase";
  }

  function formatTokenLine(advice) {
    var usage = (advice && advice.usage) || {};
    var prompt = Number(usage.prompt_tokens) || 0;
    var completion = Number(usage.completion_tokens) || 0;
    var total = Number(usage.total_tokens) || 0;
    var estimate = Number(advice && advice.prompt_estimate) || 0;

    if (!prompt && !completion && !total) {
      return (
        "LLM tokens: ~" + estimate +
        " estimated prompt -> usage not reported by the API"
      );
    }
    return (
      "LLM tokens: ~" + estimate + " estimated prompt -> " +
      prompt + " prompt + " + completion + " completion = " + total + " total"
    );
  }

  /* One fetch that turns a JSON error into a thrown Error. */
  function api(path, init) {
    return fetch(path, init || {}).then(function (response) {
      return response.text().then(function (text) {
        var data = {};
        if (text) {
          try {
            data = JSON.parse(text);
          } catch (err) {
            data = {};
          }
        }
        if (!response.ok) {
          var message = data && data.error
            ? data.error
            : "The request failed (" + response.status + ").";
          throw new Error(message);
        }
        return data;
      });
    });
  }

  /* --- The form ---------------------------------------------------------- */

  function buildGoalOptions() {
    clear(els.goal);
    var goals = options.goals || {};
    Object.keys(goals).forEach(function (key) {
      var option = el("option", null, goals[key]);
      option.value = key;
      els.goal.appendChild(option);
    });

    var wanted = (options.defaults && options.defaults.goal) || "";
    if (wanted && goals[wanted]) {
      els.goal.value = wanted;
    }
  }

  function buildConstraintChecks() {
    clear(els.constraints);
    var constraints = options.constraints || {};
    Object.keys(constraints).forEach(function (key) {
      var label = el("label", "check");
      var box = document.createElement("input");
      box.type = "checkbox";
      box.value = key;
      box.name = "constraints";
      label.appendChild(box);
      label.appendChild(el("span", null, constraints[key]));
      els.constraints.appendChild(label);
    });
  }

  function checkedConstraints() {
    var boxes = els.constraints.querySelectorAll("input[type=checkbox]");
    var chosen = [];
    Array.prototype.forEach.call(boxes, function (box) {
      if (box.checked) {
        chosen.push(box.value);
      }
    });
    return chosen;
  }

  function applyDefaults() {
    var defaults = options.defaults || {};
    if (defaults.budget !== undefined && els.budget.value === "") {
      els.budget.value = defaults.budget;
    }
    if (defaults.timeline_weeks !== undefined && els.weeks.value === "") {
      els.weeks.value = defaults.timeline_weeks;
    }

    /* Live prices are only offered when the server allows them. */
    var body = document.body;
    var allowed = body.getAttribute("data-pricing-enabled") === "true";
    els.usePricing.disabled = !allowed;
    els.usePricing.checked = allowed;
  }

  function loadOptions() {
    return api("/api/options")
      .then(function (data) {
        options = {
          goals: data.goals || {},
          constraints: data.constraints || {},
          defaults: data.defaults || {}
        };
        buildGoalOptions();
        buildConstraintChecks();
        applyDefaults();
      })
      .catch(function (err) {
        showError("The form could not be loaded: " + err.message);
      });
  }

  /* --- The report -------------------------------------------------------- */

  function renderStackTable(stack) {
    var wrap = el("div", "table-wrap");
    var table = el("table", "stack-table");

    var head = el("thead");
    var headRow = el("tr");
    ["Name", "Category", "Purpose", "Monthly cost"].forEach(function (title) {
      headRow.appendChild(el("th", null, title));
    });
    head.appendChild(headRow);
    table.appendChild(head);

    var body = el("tbody");
    (stack || []).forEach(function (item) {
      var row = el("tr");
      row.appendChild(el("td", "name", item.name));
      var category = el("td");
      category.appendChild(el("span", "pill", item.category));
      row.appendChild(category);
      row.appendChild(el("td", "purpose", item.purpose));
      row.appendChild(el("td", "cost", formatCost(item.cost_month)));
      body.appendChild(row);
    });
    table.appendChild(body);

    wrap.appendChild(table);
    return wrap;
  }

  function renderBullets(items) {
    var list = el("ul", "bullets");
    (items || []).forEach(function (item) {
      list.appendChild(el("li", null, item));
    });
    return list;
  }

  function renderLlm(advice) {
    var block = el("div", "llm-block");

    block.appendChild(el("h3", null, "LLM reasoning"));
    block.appendChild(el("p", null, advice.summary || "(no summary)"));
    if (advice.confidence) {
      block.appendChild(el("p", "confidence", "Confidence: " + advice.confidence));
    }
    if (advice.risks && advice.risks.length) {
      block.appendChild(el("h3", null, "Risks"));
      block.appendChild(renderBullets(advice.risks));
    }
    if (advice.suggestions && advice.suggestions.length) {
      block.appendChild(el("h3", null, "Suggestions"));
      block.appendChild(renderBullets(advice.suggestions));
    }
    block.appendChild(el("p", "token-line", formatTokenLine(advice)));
    return block;
  }

  /* One report - used for a fresh plan and for a saved plan. */
  function renderReport(data, meta) {
    meta = meta || {};
    clear(els.results);

    var profile = data.profile || {};
    var budget = Number(profile.budget) || 0;
    var monthlyCost = Number(data.monthly_cost) || 0;
    var goalLabel = options.goals[profile.goal] || profile.goal || "";

    var head = el("div", "result-head");
    head.appendChild(el("h3", null, meta.title || goalLabel || "Your stack"));

    var badge = el("span", "badge " + (meta.badgeClass || "badge-estimate"), meta.badge || "Rough estimates");
    head.appendChild(badge);
    els.results.appendChild(head);

    var verdict = budgetVerdict(monthlyCost, budget);
    var costLine = el(
      "p",
      "cost-line " + (monthlyCost <= budget ? "under" : "over"),
      "Estimated monthly cost: " + formatCost(monthlyCost) +
        " vs " + formatAmount(budget) + "/mo budget — " + verdict
    );
    els.results.appendChild(costLine);

    if (profile.timeline_weeks) {
      els.results.appendChild(
        el("p", "hint", "Timeline: " + profile.timeline_weeks + " weeks")
      );
    }

    els.results.appendChild(el("h3", null, "Recommended stack"));
    els.results.appendChild(renderStackTable(data.stack));

    els.results.appendChild(el("h3", null, "Why this stack"));
    if (data.applied_rules && data.applied_rules.length) {
      var rules = el("ul", "bullets");
      data.applied_rules.forEach(function (rule) {
        var text = rule.description || rule.id || "";
        if (rule.note) {
          text += " (" + rule.note + ")";
        }
        rules.appendChild(el("li", null, text));
      });
      els.results.appendChild(rules);
    } else {
      els.results.appendChild(el("p", "empty", "No extra constraints were applied."));
    }

    els.results.appendChild(el("h3", null, "Tips"));
    els.results.appendChild(renderBullets(data.notes));

    if (data.llm) {
      els.results.appendChild(renderLlm(data.llm));
    }
    if (data.llm_error) {
      els.results.appendChild(el("p", "notice notice-warn", data.llm_error));
    }
    if (data.saved_as) {
      els.results.appendChild(
        el("p", "hint", "Saved as '" + data.saved_as + "'.")
      );
    }

    if (meta.planName) {
      var actions = el("div", "actions");
      var refresh = el("button", "secondary", "Get fresh LLM advice");
      refresh.type = "button";
      refresh.addEventListener("click", function () {
        refreshPlan(meta.planName);
      });
      actions.appendChild(refresh);

      var close = el("button", "secondary", "Close");
      close.type = "button";
      close.addEventListener("click", function () {
        els.resultsPanel.hidden = true;
        openPlan = null;
      });
      actions.appendChild(close);
      els.results.appendChild(actions);
    }

    els.resultsPanel.hidden = false;
  }

  /* --- Asking for a plan ------------------------------------------------- */

  function submitPlan(event) {
    if (event) {
      event.preventDefault();
    }
    clearError();
    els.submit.disabled = true;

    var body = {
      goal: els.goal.value,
      budget: Number(els.budget.value),
      timeline_weeks: Number(els.weeks.value),
      constraints: checkedConstraints(),
      use_llm: els.useLlm.checked,
      use_pricing: els.usePricing.checked
    };

    var saveAs = els.saveAs.value.trim();
    if (saveAs) {
      body.save_as = saveAs;
    }

    api("/api/plan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    })
      .then(function (data) {
        openPlan = null;
        var badge = data.pricing === "live"
          ? { badge: "Live Azure prices", badgeClass: "badge-live" }
          : { badge: "Rough estimates", badgeClass: "badge-estimate" };
        renderReport(data, badge);
        if (data.saved_as) {
          loadPlans();
        }
      })
      .catch(function (err) {
        showError(err.message);
      })
      .then(function () {
        els.submit.disabled = false;
      });
  }

  /* --- The saved plans --------------------------------------------------- */

  function renderPlans(plans) {
    clear(els.plans);

    if (!plans || !plans.length) {
      els.plans.appendChild(el("p", "empty", "No saved plans yet."));
      return;
    }

    var list = el("ul", "plan-list");
    plans.forEach(function (plan) {
      var item = el("li");
      item.appendChild(el("span", "plan-name", plan.name));

      var goalLabel = options.goals[plan.goal] || plan.goal || "";
      item.appendChild(
        el("span", "plan-meta", goalLabel + " - " + formatCost(plan.monthly_cost))
      );

      var actions = el("span", "plan-actions");

      var open = el("button", "secondary", "Open");
      open.type = "button";
      open.addEventListener("click", function () {
        loadPlan(plan.name);
      });
      actions.appendChild(open);

      var remove = el("button", "danger", "Delete");
      remove.type = "button";
      remove.addEventListener("click", function () {
        deletePlan(plan.name);
      });
      actions.appendChild(remove);

      item.appendChild(actions);
      list.appendChild(item);
    });

    els.plans.appendChild(list);
  }

  function loadPlans() {
    return api("/api/plans")
      .then(function (data) {
        renderPlans(data.plans || []);
      })
      .catch(function (err) {
        showError("The saved plans could not be loaded: " + err.message);
      });
  }

  function loadPlan(name) {
    clearError();
    return api("/api/plans/" + encodeURIComponent(name))
      .then(function (data) {
        openPlan = name;
        renderReport(data, {
          title: "Saved plan: " + data.name,
          badge: "Saved plan",
          badgeClass: "badge-saved",
          planName: name
        });
      })
      .catch(function (err) {
        showError(err.message);
      });
  }

  function deletePlan(name) {
    clearError();
    return api("/api/plans/" + encodeURIComponent(name), { method: "DELETE" })
      .then(function () {
        if (openPlan === name) {
          els.resultsPanel.hidden = true;
          openPlan = null;
        }
        return loadPlans();
      })
      .catch(function (err) {
        showError(err.message);
      });
  }

  function refreshPlan(name) {
    clearError();
    return api("/api/plans/" + encodeURIComponent(name) + "/refresh", { method: "POST" })
      .then(function (data) {
        renderReport(data, {
          title: "Saved plan: " + data.name,
          badge: "Saved plan",
          badgeClass: "badge-saved",
          planName: name
        });
      })
      .catch(function (err) {
        showError(err.message);
      });
  }

  /* --- Start ------------------------------------------------------------- */

  els.form.addEventListener("submit", submitPlan);
  loadOptions().then(loadPlans);
})();
