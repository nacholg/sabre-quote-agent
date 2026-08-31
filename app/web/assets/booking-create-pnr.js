(() => {
  "use strict";

  const bookingId = decodeURIComponent(
    window.location.pathname.split("/").filter(Boolean).pop() || ""
  );
  const $ = id => document.getElementById(id);

  let latestReview = null;
  let clientRequestId = null;
  let writeUncertain = false;

  class ApiError extends Error {
    constructor(message, status) {
      super(message);
      this.status = status;
    }
  }

  async function api(url, options = {}) {
    const response = await fetch(url, options);
    const type = response.headers.get("content-type") || "";
    const payload = type.includes("application/json")
      ? await response.json()
      : await response.text();

    if (!response.ok) {
      const detail = payload?.detail || payload || `HTTP ${response.status}`;
      throw new ApiError(
        typeof detail === "string" ? detail : JSON.stringify(detail),
        response.status
      );
    }
    return payload;
  }

  function statusMessage(message, kind = "info") {
    const node = $("createPnrStatus");
    if (!node) return;

    if (!message) {
      node.textContent = "";
      node.className = "message hidden";
      return;
    }

    node.textContent = message;
    node.className = `message ${kind}`;
  }

  function bookingStatusLabel(status) {
    return {
      ready_for_review: "Lista para crear PNR",
      revalidation_required: "Revalidación requerida",
      requires_agent_action: "Requiere acción",
      ready_to_create_pnr: "Lista para crear PNR",
      pnr_created: "PNR creado",
    }[status] || status || "Booking";
  }

  function canOpen(review) {
    return Boolean(
      review?.passengers_complete &&
      review?.contact_complete &&
      [
        "ready_for_review",
        "revalidation_required",
        "requires_agent_action",
        "ready_to_create_pnr",
        "pnr_created",
      ].includes(review.status)
    );
  }

  function setActiveStep() {
    document.querySelectorAll("[data-funnel-step]").forEach(step => {
      const active = step.dataset.funnelStep === "create-pnr";
      step.classList.toggle("active", active);
      if (active) {
        step.setAttribute("aria-current", "step");
      } else {
        step.removeAttribute("aria-current");
      }
    });
  }

  function showPanel() {
    document.querySelectorAll("[data-funnel-panel]").forEach(panel => {
      panel.classList.toggle(
        "hidden",
        panel.dataset.funnelPanel !== "create-pnr"
      );
    });
  }

  async function loadReview() {
    latestReview = await api(
      `/bookings/${encodeURIComponent(bookingId)}/review`
    );
    return latestReview;
  }

  async function getAttempt() {
    try {
      return await api(
        `/bookings/${encodeURIComponent(bookingId)}/pnr`
      );
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) {
        return null;
      }
      throw error;
    }
  }

  function renderReady(review) {
    const badge = $("createPnrBadge");
    if (badge) {
      badge.textContent = "Listo para verificar";
      badge.className = "dev-badge";
    }

    $("createPnrContent").innerHTML = `
      <section class="revalidation-result neutral">
        <div class="revalidation-result-head">
          <div>
            <span class="eyebrow">Control final</span>
            <h3>Revalidación automática + Create PNR</h3>
          </div>
          <strong>${bookingStatusLabel(review.status)}</strong>
        </div>

        <p>
          Al confirmar, Sabre volverá a validar el producto exacto.
          Sólo si vuelos, clases, tarifa branded, moneda y precio coinciden,
          se enviará Create Booking.
        </p>

        <div class="revalidation-no-change">
          No se realizará ninguna sustitución silenciosa de tarifa,
          brand, clase o itinerario.
        </div>
      </section>
    `;

    const button = $("createPnrButton");
    if (button) {
      button.disabled = writeUncertain;
      button.textContent = "Crear PNR";
    }
  }

  function renderAttempt(attempt) {
    clientRequestId = attempt.client_request_id || clientRequestId;

    const status = attempt.status;
    const badge = $("createPnrBadge");
    const button = $("createPnrButton");

    const labels = {
      prepared: "Preparado",
      submitting: "Enviando",
      succeeded: "PNR creado",
      failed_safe: "Fallo seguro",
      reconciliation_required: "Reconciliación requerida",
    };

    if (badge) {
      badge.textContent = labels[status] || status;
      badge.className = `dev-badge revalidation-badge ${
        status === "succeeded"
          ? "matched"
          : status === "reconciliation_required"
            ? "danger"
            : "neutral"
      }`;
    }

    $("createPnrContent").innerHTML = `
      <section class="revalidation-result ${
        status === "succeeded" ? "matched" : "neutral"
      }">
        <div class="revalidation-result-head">
          <div>
            <span class="eyebrow">Create PNR</span>
            <h3>${labels[status] || status}</h3>
          </div>
          <strong>${attempt.confirmation_id || "—"}</strong>
        </div>

        ${
          status === "succeeded"
            ? `
              <div class="message ok">
                PNR creado correctamente.
                Localizador Sabre: ${attempt.confirmation_id}
              </div>
            `
            : ""
        }

        ${
          status === "reconciliation_required"
            ? `
              <div class="message error">
                El resultado es ambiguo. No reintentes Create PNR.
                El intento debe reconciliarse antes de continuar.
              </div>
            `
            : ""
        }

        ${
          attempt.error_message
            ? `<div class="revalidation-error-detail">${attempt.error_message}</div>`
            : ""
        }
      </section>
    `;

    if (button) {
      if (
        status === "succeeded" ||
        status === "submitting" ||
        status === "reconciliation_required"
      ) {
        button.disabled = true;
      } else {
        button.disabled = false;
      }

      button.textContent = status === "failed_safe"
        ? "Reintentar intento seguro"
        : status === "succeeded"
          ? "PNR creado"
          : "Crear PNR";
    }

    if ($("bookingStatus") && status === "succeeded") {
      $("bookingStatus").textContent = "PNR creado";
    }
  }

  async function renderLatestRevalidationFailure() {
    try {
      const data = await api(
        `/bookings/${encodeURIComponent(bookingId)}/revalidation`
      );

      const changes = Array.isArray(data.diff?.changes)
        ? data.diff.changes
        : [];

      if (!changes.length) return;

      const rows = changes.map(change => `
        <div class="revalidation-change-row">
          <strong>${String(change.field || "Cambio")}</strong>
          <div class="revalidation-comparison">
            <span>
              <small>Antes</small>
              ${String(change.before ?? "—")}
            </span>
            <span>
              <small>Ahora</small>
              ${String(change.after ?? "—")}
            </span>
          </div>
        </div>
      `).join("");

      $("createPnrContent").innerHTML = `
        <section class="revalidation-result changed">
          <div class="revalidation-result-head">
            <div>
              <span class="eyebrow">Create PNR detenido</span>
              <h3>La selección exacta cambió</h3>
            </div>
            <strong>${data.revalidation_status}</strong>
          </div>
          <p>
            Create Booking no fue enviado. Revisá las diferencias antes
            de continuar.
          </p>
          <div class="revalidation-changes">${rows}</div>
        </section>
      `;
    } catch {
      // Keep the original server error visible.
    }
  }

  async function openCreatePnr() {
    statusMessage("Verificando estado del Booking…", "info");

    try {
      const review = await loadReview();

      if (!canOpen(review)) {
        throw new Error(
          "Pasajeros y contacto deben estar completos antes de Crear PNR."
        );
      }

      setActiveStep();
      showPanel();

      const attempt = await getAttempt();
      if (attempt) {
        renderAttempt(attempt);
      } else {
        renderReady(review);
      }

      statusMessage("");
      $("createPnrPanel")?.scrollIntoView({
        behavior: "smooth",
        block: "start",
      });
    } catch (error) {
      statusMessage(error.message || String(error), "error");
    }
  }

  async function createPnr() {
    if (writeUncertain) return;

    const confirmed = window.confirm(
      "Sabre revalidará la selección exacta y, si coincide, creará el PNR. ¿Continuar?"
    );
    if (!confirmed) return;

    const button = $("createPnrButton");
    if (button) {
      button.disabled = true;
      button.textContent = "Revalidando automáticamente…";
    }

    statusMessage(
      "Revalidando automáticamente con Sabre. Si todo coincide, se creará el PNR…",
      "info"
    );

    try {
      const review = await loadReview();

      if (!canOpen(review) || review.status === "pnr_created") {
        const existing = await getAttempt();
        if (existing) {
          renderAttempt(existing);
          statusMessage("");
          return;
        }
        throw new Error("El Booking ya no está habilitado para Create PNR.");
      }

      if (!clientRequestId) {
        clientRequestId = crypto.randomUUID();
      }

      const attempt = await api(
        `/bookings/${encodeURIComponent(bookingId)}/pnr`,
        {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            revision: review.booking_revision,
            client_request_id: clientRequestId,
          }),
        }
      );

      renderAttempt(attempt);

      if (attempt.status === "succeeded") {
        statusMessage(
          `PNR creado correctamente: ${attempt.confirmation_id}`,
          "ok"
        );
      }
    } catch (error) {
      // A server response is definitive. A network failure is not.
      if (!(error instanceof ApiError)) {
        try {
          const recovered = await getAttempt();
          if (recovered) {
            renderAttempt(recovered);
            statusMessage(
              "La respuesta original se perdió, pero el intento persistido fue recuperado.",
              recovered.status === "succeeded" ? "ok" : "info"
            );
            return;
          }
        } catch {
          // Fall through to the ambiguous browser state below.
        }

        writeUncertain = true;
        if (button) {
          button.disabled = true;
          button.textContent = "Verificación requerida";
        }
        statusMessage(
          "No se pudo confirmar el resultado del envío. No reintentes. Recargá la página para recuperar el intento persistido.",
          "error"
        );
        return;
      }

      statusMessage(error.message || String(error), "error");

      try {
        const existing = await getAttempt();
        if (existing) {
          renderAttempt(existing);
          return;
        }
      } catch {
        // Keep the original API error.
      }

      await renderLatestRevalidationFailure();

      if (button) {
        button.disabled = false;
        button.textContent = "Crear PNR";
      }
    }
  }

  function backToReview() {
    if (window.bookingFunnel?.openStep) {
      window.bookingFunnel.openStep("review");
    }
  }

  window.addEventListener("booking:review-state", event => {
    latestReview = event.detail || latestReview;
  });

  $("continueCreatePnrButton")?.addEventListener(
    "click",
    openCreatePnr
  );

  $("createPnrButton")?.addEventListener(
    "click",
    createPnr
  );

  $("backToReviewButton")?.addEventListener(
    "click",
    backToReview
  );

  document
    .querySelector('[data-funnel-step="create-pnr"]')
    ?.addEventListener("click", openCreatePnr);
})();
