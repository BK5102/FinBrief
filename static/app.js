(() => {
  const form = document.querySelector("[data-refresh-form]");
  const button = document.querySelector("[data-refresh-button]");
  const status = document.querySelector("[data-refresh-status]");

  if (!form || !button || !status) {
    return;
  }

  const setStatus = (message, tone, running) => {
    status.textContent = message;
    status.classList.remove("running", "success", "failure");
    if (tone) {
      status.classList.add(tone);
    }
    status.dataset.running = running ? "true" : "false";
    button.disabled = running;
    button.textContent = running ? "Refresh Running" : "Run Refresh";
  };

  const describe = (refresh) => {
    if (refresh.running) {
      return {
        message: `Refresh running since ${refresh.started_at}.`,
        tone: "running",
        running: true,
      };
    }
    if (refresh.status === "success") {
      return {
        message: `Last manual refresh completed at ${refresh.completed_at}. Reloading dashboard...`,
        tone: "success",
        running: false,
      };
    }
    if (refresh.status === "failure") {
      return {
        message: `Last manual refresh failed: ${refresh.error}`,
        tone: "failure",
        running: false,
      };
    }
    return {
      message: "Manual refresh is idle.",
      tone: "",
      running: false,
    };
  };

  const poll = async () => {
    try {
      const response = await fetch("/refresh/status", {
        headers: { Accept: "application/json" },
      });
      const refresh = await response.json();
      const view = describe(refresh);
      setStatus(view.message, view.tone, view.running);
      if (refresh.running) {
        window.setTimeout(poll, 2500);
      } else if (refresh.status === "success") {
        window.setTimeout(() => window.location.reload(), 1200);
      }
    } catch (error) {
      setStatus(`Could not read refresh status: ${error}`, "failure", false);
    }
  };

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    setStatus("Starting refresh...", "running", true);
    try {
      const response = await fetch("/refresh", {
        method: "POST",
        headers: { Accept: "application/json" },
      });
      const payload = await response.json();
      const view = describe(payload.refresh);
      setStatus(view.message, view.tone, view.running);
      window.setTimeout(poll, 1000);
    } catch (error) {
      setStatus(`Could not start refresh: ${error}`, "failure", false);
    }
  });

  if (status.dataset.running === "true") {
    window.setTimeout(poll, 1000);
  }
})();
