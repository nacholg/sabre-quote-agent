(() => {
  "use strict";

  const bookingId = decodeURIComponent(
    window.location.pathname.split("/").filter(Boolean).pop() || ""
  );
  const $ = id => document.getElementById(id);

  let latestReview = null;
  let latestRevalidation = null;

  function esc(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  async function api(url, options = {}) {
    const response = await fetch(url, options);
    const type = response.headers.get("content-type") || "";
    const payload = type.includes("application/json")
      ? await response.json()
      : await response.text();

    if (!response.ok) {
      const detail = payload?.detail || payload || `HTTP ${response.status}`;
      throw new Error(
        typeof detail === "string" ? detail : JSON.stringify(detail)
      );
    }
    return payload;
  }

  function bookingStatusLabel(status) {
    return {
      draft: "Borrador",
      ready_for_review: "Lista para review",
      revalidation_required: "Revalidación requerida",
      requires_agent_action: "Requiere acción",
      ready_to_create_pnr: "Lista para crear PNR",
      abandoned: "Abandonada",
      pnr_created: "PNR creado",
    }[status] || status || "Booking";
  }

  function revalidationLabel(status) {
    return {
      not_run: "Sin revalidar",
      matched: "Coincide",
      price_changed: "Cambió el precio",
      fare_changed: "Cambió la tarifa",
      itinerary_changed: "Cambió el itinerario",
      unavailable: "Sin disponibilidad",
      error: "Error",
      stale: "Revalidación vencida",
    }[status] || status || "Sin revalidar";
  }

  function fieldLabel(field) {
    return {
      total_price: "Precio total",
      fare_basis_codes: "Fare basis",
      brand: "Brand",
      cabin: "Cabina",
      currency: "Moneda",
      validating_carrier: "Validating carrier",
      itinerary: "Itinerario",
      availability: "Disponibilidad",
    }[field] || field || "Cambio";
  }

  function statusMessage(message, kind = "info") {
    const node = $("revalidationStatus");
    if (!node) return;

    if (!message) {
      node.textContent = "";
      node.className = "message hidden";
      return;
    }

    node.textContent = message;
    node.className = `message ${kind}`;
  }

  function setActiveStep() {
    document.querySelectorAll("[data-funnel-step]").forEach(step => {
      const active = step.dataset.funnelStep === "revalidation";
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
        panel.dataset.funnelPanel !== "revalidation"
      );
    });
  }

  function canOpenFromReview(review) {
    return Boolean(
      review?.passengers_complete &&
      review?.contact_complete &&
      [
        "ready_for_review",
        "revalidation_required",
        "requires_agent_action",
        "ready_to_create_pnr",
      ].includes(review.status)
    );
  }

  function resultClass(status) {
    if (status === "matched") return "matched";
    if (status === "not_run") return "neutral";
    if (status === "error" || status === "unavailable") return "danger";
    if (status === "stale") return "stale";
    return "changed";
  }

  function stringifyValue(value) {
    if (value == null || value === "") return "—";
    if (Array.isArray(value)) {
      if (
        value.length &&
        Array.isArray(value[0])
      ) {
        return value
          .map(segment => segment.filter(Boolean).join(" · "))
          .join(" | ");
      }
      return value.join(" / ");
    }
    if (typeof value === "object") {
      return JSON.stringify(value);
    }
    return String(value);
  }

  function renderChanges(diff, status) {
    const changes = Array.isArray(diff?.changes) ? diff.changes : [];
    if (!changes.length) {
      if (status !== "matched") return "";
      return `
        <div class="revalidation-no-change">
          No se detectaron diferencias comerciales ni de itinerario.
        </div>
      `;
    }

    return changes.map(change => `
      <div class="revalidation-change-row">
        <strong>${esc(fieldLabel(change.field))}</strong>
        <div class="revalidation-comparison">
          <span>
            <small>Antes</small>
            ${esc(stringifyValue(change.before))}
          </span>
          <span>
            <small>Ahora</small>
            ${esc(stringifyValue(change.after))}
          </span>
        </div>
      </div>
    `).join("");
  }

  function resultDescription(data) {
    return {
      matched:
        "Sabre confirmó el mismo itinerario, booking class y producto tarifario.",
      price_changed:
        "El vuelo sigue disponible, pero Sabre devolvió un precio diferente.",
      fare_changed:
        "Sabre devolvió una identidad tarifaria diferente.",
      itinerary_changed:
        "El itinerario exacto congelado ya no coincide con la respuesta revalidada.",
      unavailable:
        "Sabre indicó que el producto exacto ya no está disponible.",
      error:
        "Sabre no pudo completar una revalidación concluyente.",
      stale:
        "Este resultado quedó inválido porque el Booking fue modificado después.",
      not_run:
        "Todavía no se consultó Sabre desde este Booking.",
    }[data.revalidation_status] || "Estado de revalidación disponible.";
  }

  function renderRevalidation(data) {
    latestRevalidation = data;
    const status = data.revalidation_status || "not_run";
    const badge = $("revalidationBadge");

    if (badge) {
      badge.textContent = revalidationLabel(status);
      badge.className = `dev-badge revalidation-badge ${resultClass(status)}`;
    }

    const checked = data.checked_at
      ? new Date(data.checked_at).toLocaleString("es-AR")
      : "Pendiente";
    const sourceRevision = data.source_offer_revision_id ?? "—";
    const candidateRevision = data.candidate_offer_revision_id ?? "—";
    const changesHtml = renderChanges(data.diff, status);

    $("revalidationContent").innerHTML = `
      <section class="revalidation-result ${resultClass(status)}">
        <div class="revalidation-result-head">
          <div>
            <span class="eyebrow">Resultado</span>
            <h3>${esc(revalidationLabel(status))}</h3>
          </div>
          <strong>${esc(bookingStatusLabel(data.status))}</strong>
        </div>

        <p>${esc(resultDescription(data))}</p>

        <div class="revalidation-meta-grid">
          <div>
            <small>Último control</small>
            <strong>${esc(checked)}</strong>
          </div>
          <div>
            <small>Referencia Sabre</small>
            <strong>${esc(data.provider_reference || "—")}</strong>
          </div>
          <div>
            <small>ID oferta origen</small>
            <strong>${esc(sourceRevision)}</strong>
          </div>
          <div>
            <small>ID oferta candidata</small>
            <strong>${esc(candidateRevision)}</strong>
          </div>
        </div>

        ${
          changesHtml
            ? `<div class="revalidation-changes">${changesHtml}</div>`
            : ""
        }

        ${
          data.error_message
            ? `<div class="revalidation-error-detail">${esc(data.error_message)}</div>`
            : ""
        }
      </section>

      ${
        status === "matched"
          ? `
            <div class="message ok revalidation-next">
              Producto revalidado. El Booking quedó listo para Create PNR,
              que se habilitará en v0.32.
            </div>
          `
          : ""
      }
    `;

    $("bookingStatus").textContent = bookingStatusLabel(data.status);

    const button = $("runRevalidationButton");
    if (button) {
      button.disabled = false;
      button.textContent = data.revalidation_id
        ? "Revalidar nuevamente"
        : "Revalidar con Sabre";
    }
  }

  async function loadReview() {
    latestReview = await api(
      `/bookings/${encodeURIComponent(bookingId)}/review`
    );
    return latestReview;
  }

  async function loadLatestRevalidation() {
    const data = await api(
      `/bookings/${encodeURIComponent(bookingId)}/revalidation`
    );
    renderRevalidation(data);
    return data;
  }

  async function openRevalidation() {
    statusMessage("Verificando estado del Booking…", "info");

    try {
      const review = await loadReview();
      if (!canOpenFromReview(review)) {
        statusMessage(
          "Pasajeros y contacto deben estar completos antes de revalidar.",
          "error"
        );
        return;
      }

      setActiveStep();
      showPanel();
      await loadLatestRevalidation();
      statusMessage("");
      $("revalidationPanel")?.scrollIntoView({
        behavior: "smooth",
        block: "start",
      });
    } catch (error) {
      statusMessage(error.message || String(error), "error");
    }
  }

  async function refreshAcceptedRevision() {
    try {
      const booking = await api(
        `/bookings/${encodeURIComponent(bookingId)}`
      );
      $("bookingStatus").textContent = bookingStatusLabel(booking.status);

      const accepted = booking.accepted_offer_revision;
      if (accepted) {
        if ($("offerRevision")) {
          $("offerRevision").textContent = `#${accepted.revision_number}`;
        }
        if ($("revisionNumber")) {
          $("revisionNumber").textContent = `#${accepted.revision_number}`;
        }
      }
    } catch {
      // Revalidation result already contains the authoritative Booking state.
    }
  }

  async function runRevalidation() {
    const button = $("runRevalidationButton");
    if (button) {
      button.disabled = true;
      button.textContent = "Revalidando…";
    }

    statusMessage(
      "Consultando Sabre. Esto puede tardar algunos segundos…",
      "info"
    );

    try {
      const review = await loadReview();
      if (!canOpenFromReview(review)) {
        throw new Error(
          "El Booking ya no está listo para revalidar. Recargá Review."
        );
      }

      const data = await api(
        `/bookings/${encodeURIComponent(bookingId)}/revalidation`,
        {
          method: "POST",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            revision: review.booking_revision,
          }),
        }
      );

      renderRevalidation(data);
      window.dispatchEvent(
        new CustomEvent("booking:revision-changed", {
          detail: {booking_revision: data.booking_revision},
        })
      );
      await refreshAcceptedRevision();

      const kind = data.revalidation_status === "matched" ? "ok" : "info";
      statusMessage(
        data.revalidation_status === "matched"
          ? "Revalidación completada: el producto coincide ✓"
          : "Revalidación completada. Revisá el resultado antes de continuar.",
        kind
      );
    } catch (error) {
      statusMessage(error.message || String(error), "error");
      try {
        await loadLatestRevalidation();
      } catch {
        // Keep the original error visible.
      }
    } finally {
      if (button && button.disabled) {
        button.disabled = false;
        button.textContent = latestRevalidation?.revalidation_id
          ? "Revalidar nuevamente"
          : "Revalidar con Sabre";
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

  $("continueRevalidationButton")?.addEventListener(
    "click",
    openRevalidation
  );
  $("runRevalidationButton")?.addEventListener(
    "click",
    runRevalidation
  );
  $("backToReviewButton")?.addEventListener(
    "click",
    backToReview
  );
  document
    .querySelector('[data-funnel-step="revalidation"]')
    ?.addEventListener("click", openRevalidation);
})();
