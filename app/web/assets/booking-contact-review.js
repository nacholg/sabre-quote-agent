(() => {
  "use strict";

  const bookingId = decodeURIComponent(
    window.location.pathname.split("/").filter(Boolean).pop() || ""
  );
  const $ = id => document.getElementById(id);

  let contactState = null;
  let passengersComplete = false;
  let currentStep = "passengers";

  function esc(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function money(value, currency) {
    const amount = Number(value);
    if (!Number.isFinite(amount)) return "Consultar";
    try {
      return new Intl.NumberFormat("es-AR", {
        style: "currency",
        currency: currency || "USD",
        maximumFractionDigits: 2,
      }).format(amount);
    } catch {
      return `${currency || ""} ${amount.toFixed(2)}`.trim();
    }
  }

  function statusLabel(status) {
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

  function contactStatus(message, kind = "info") {
    const node = $("contactStatus");
    if (!node) return;
    if (!message) {
      node.textContent = "";
      node.className = "message hidden";
      return;
    }
    node.textContent = message;
    node.className = `message ${kind}`;
  }

  function reviewStatus(message, kind = "info") {
    const node = $("reviewStatus");
    if (!node) return;
    if (!message) {
      node.textContent = "";
      node.className = "message hidden";
      return;
    }
    node.textContent = message;
    node.className = `message ${kind}`;
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

  function setActiveStep(stepName) {
    document.querySelectorAll("[data-funnel-step]").forEach(step => {
      const active = step.dataset.funnelStep === stepName;
      step.classList.toggle("active", active);
      if (active) {
        step.setAttribute("aria-current", "step");
      } else {
        step.removeAttribute("aria-current");
      }
    });
  }

  function showPanel(stepName) {
    document.querySelectorAll("[data-funnel-panel]").forEach(panel => {
      panel.classList.toggle(
        "hidden",
        panel.dataset.funnelPanel !== stepName
      );
    });
  }

  async function openStep(stepName) {
    if (stepName === "revalidation") return;

    if (
      (stepName === "contact" || stepName === "review") &&
      !passengersComplete
    ) {
      contactStatus(
        "Completá pasajeros antes de avanzar a Contacto.",
        "info"
      );
      stepName = "passengers";
    }

    if (stepName === "review" && !contactState?.complete) {
      contactStatus(
        "Guardá un contacto completo antes de avanzar a Review.",
        "info"
      );
      stepName = "contact";
    }

    currentStep = stepName;
    setActiveStep(stepName);
    showPanel(stepName);

    if (stepName === "contact") {
      await loadContact();
    }
    if (stepName === "review") {
      await loadReview();
    }

    document.querySelector(
      `[data-funnel-panel="${stepName}"]`
    )?.scrollIntoView({behavior: "smooth", block: "start"});
  }

  window.bookingFunnel = {openStep};

  function renderContact(data) {
    contactState = data;

    $("contactName").value = data.name || "";
    $("contactEmail").value = data.email || "";
    $("contactCountryCode").value = data.phone_country_code || "";
    $("contactPhone").value = data.phone_number || "";
    $("preferredChannel").value = data.preferred_channel || "";

    const badge = $("contactCompletionBadge");
    if (badge) {
      badge.textContent = data.complete
        ? "Contacto completo"
        : "Datos pendientes";
      badge.classList.toggle("complete-badge", Boolean(data.complete));
    }

    const continueButton = $("continueReviewButton");
    if (continueButton) {
      continueButton.disabled = !(data.complete && passengersComplete);
    }
  }

  async function loadContact() {
    if (!bookingId) return;
    contactStatus("Cargando contacto…", "info");

    try {
      const data = await api(
        `/bookings/${encodeURIComponent(bookingId)}/contact`
      );
      renderContact(data);
      contactStatus("");
    } catch (error) {
      contactStatus(error.message || String(error), "error");
    }
  }

  function contactPayload() {
    return {
      revision: contactState.booking_revision,
      name: $("contactName").value.trim() || null,
      email: $("contactEmail").value.trim() || null,
      phone_country_code:
        $("contactCountryCode").value.trim() || null,
      phone_number: $("contactPhone").value.trim() || null,
      preferred_channel:
        $("preferredChannel").value || null,
    };
  }

  async function refreshBookingHeader() {
    try {
      const booking = await api(
        `/bookings/${encodeURIComponent(bookingId)}`
      );
      $("bookingStatus").textContent = statusLabel(booking.status);
    } catch {
      // The main workspace already owns global load errors.
    }
  }

  async function saveContact(event) {
    event.preventDefault();

    const form = $("contactForm");
    if (!form || !contactState) return;
    if (!form.reportValidity()) return;

    const button = $("saveContactButton");
    if (button) {
      button.disabled = true;
      button.textContent = "Guardando…";
    }
    contactStatus("Guardando contacto…", "info");

    try {
      const data = await api(
        `/bookings/${encodeURIComponent(bookingId)}/contact`,
        {
          method: "PUT",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify(contactPayload()),
        }
      );

      renderContact(data);
      window.dispatchEvent(
        new CustomEvent("booking:revision-changed", {
          detail: {booking_revision: data.booking_revision},
        })
      );
      contactStatus(
        data.complete
          ? "Contacto guardado ✓"
          : "Contacto guardado. Todavía faltan datos.",
        data.complete ? "ok" : "info"
      );
      await refreshBookingHeader();
    } catch (error) {
      contactStatus(error.message || String(error), "error");
      if (
        String(error.message || "").includes("Recargá") ||
        String(error.message || "").includes("modificado en paralelo")
      ) {
        await loadContact();
      }
    } finally {
      if (button) {
        button.disabled = false;
        button.textContent = "Guardar contacto";
      }
    }
  }

  function passengerName(passenger) {
    return [
      passenger.given_name,
      passenger.middle_name,
      passenger.surname,
    ].filter(Boolean).join(" ");
  }

  function channelLabel(value) {
    return {
      email: "Email",
      phone: "Teléfono",
      whatsapp: "WhatsApp",
    }[value] || "Sin preferencia";
  }

  function renderReview(data) {
    const revision = data.offer_revision;
    const snapshot = revision?.snapshot || {};
    const fare = snapshot.fare || {};
    const segments = snapshot.segments || [];

    const badge = $("reviewReadinessBadge");
    if (badge) {
      badge.textContent = data.ready_for_review
        ? "Lista para Review"
        : "Datos incompletos";
      badge.classList.toggle(
        "complete-badge",
        Boolean(data.ready_for_review)
      );
    }

    $("bookingStatus").textContent = statusLabel(data.status);

    const passengerRows = (data.passengers || []).map(passenger => `
      <div class="review-row">
        <div>
          <strong>${esc(passengerName(passenger) || `Pasajero ${passenger.slot_index}`)}</strong>
          <small>
            ${esc(passenger.passenger_type)}
            ${passenger.quoted_age != null
              ? ` · edad cotizada ${esc(passenger.quoted_age)}`
              : ""}
          </small>
        </div>
        <span>${esc(passenger.date_of_birth || "DOB pendiente")}</span>
      </div>
    `).join("");

    const segmentRows = segments.map(segment => `
      <div class="review-row">
        <div>
          <strong>
            ${esc(segment.marketing_carrier)} ${esc(segment.flight_number)}
            · ${esc(segment.departure_airport)} → ${esc(segment.arrival_airport)}
          </strong>
          <small>Clase ${esc(segment.booking_class || "—")}</small>
        </div>
        <span>${esc(fare.cabin || "—")}</span>
      </div>
    `).join("");

    const phone = [
      data.contact.phone_country_code,
      data.contact.phone_number,
    ].filter(Boolean).join(" ");

    $("reviewContent").innerHTML = `
      <section class="review-block">
        <div class="review-block-head">
          <span>Producto congelado</span>
          <strong>
            ${esc(fare.brand_name || fare.brand_code || fare.cabin || "Tarifa")}
            · ${esc(money(
              fare.total_price ?? fare.price_per_passenger,
              fare.currency
            ))}
          </strong>
        </div>
        ${segmentRows}
      </section>

      <section class="review-block">
        <div class="review-block-head">
          <span>Pasajeros</span>
          <strong>${data.passengers_complete ? "Completo" : "Pendiente"}</strong>
        </div>
        ${passengerRows}
      </section>

      <section class="review-block">
        <div class="review-block-head">
          <span>Contacto</span>
          <strong>${data.contact_complete ? "Completo" : "Pendiente"}</strong>
        </div>
        <div class="review-contact-grid">
          <div>
            <small>Nombre</small>
            <strong>${esc(data.contact.name || "—")}</strong>
          </div>
          <div>
            <small>Email</small>
            <strong>${esc(data.contact.email || "—")}</strong>
          </div>
          <div>
            <small>Teléfono</small>
            <strong>${esc(phone || "—")}</strong>
          </div>
          <div>
            <small>Canal preferido</small>
            <strong>${esc(channelLabel(data.contact.preferred_channel))}</strong>
          </div>
        </div>
      </section>
    `;

    const revalidationAllowed = (
      data.passengers_complete &&
      data.contact_complete &&
      [
        "ready_for_review",
        "revalidation_required",
        "requires_agent_action",
        "ready_to_create_pnr",
      ].includes(data.status)
    );

    const revalidationButton = $("continueRevalidationButton");
    if (revalidationButton) {
      revalidationButton.disabled = !revalidationAllowed;
      revalidationButton.textContent = data.ready_for_review
        ? "Continuar a Revalidación"
        : "Ver Revalidación";
    }

    let reviewMessage = "El Booking todavía tiene datos pendientes.";
    let reviewKind = "info";
    if (data.ready_for_review) {
      reviewMessage = "Booking listo para pasar a revalidación.";
      reviewKind = "ok";
    } else if (data.status === "ready_to_create_pnr") {
      reviewMessage = "Booking revalidado y listo para crear PNR en v0.32.";
      reviewKind = "ok";
    } else if (data.status === "requires_agent_action") {
      reviewMessage = "La revalidación requiere revisión del agente.";
    } else if (data.status === "revalidation_required") {
      reviewMessage = "Los datos cambiaron y el Booking debe revalidarse otra vez.";
    }

    reviewStatus(reviewMessage, reviewKind);
    window.dispatchEvent(
      new CustomEvent("booking:review-state", {detail: data})
    );
  }

  async function loadReview() {
    reviewStatus("Armando Review…", "info");

    try {
      const data = await api(
        `/bookings/${encodeURIComponent(bookingId)}/review`
      );
      renderReview(data);
    } catch (error) {
      reviewStatus(error.message || String(error), "error");
    }
  }

  async function loadPassengerReadiness() {
    try {
      const data = await api(
        `/bookings/${encodeURIComponent(bookingId)}/passengers`
      );
      passengersComplete = Boolean(data.complete);
      if (contactState) {
        contactState.booking_revision = Math.max(
          Number(contactState.booking_revision || 0),
          Number(data.booking_revision || 0)
        );
      }
    } catch {
      passengersComplete = false;
    }
  }

  window.addEventListener("booking:passengers-state", event => {
    const data = event.detail || {};
    passengersComplete = Boolean(data.complete);
    if (contactState && data.booking_revision != null) {
      contactState.booking_revision = Math.max(
        Number(contactState.booking_revision || 0),
        Number(data.booking_revision || 0)
      );
    }
    if (!passengersComplete && currentStep !== "passengers") {
      openStep("passengers");
    }
  });

  window.addEventListener("booking:revision-changed", event => {
    const revision = Number(event.detail?.booking_revision);
    if (
      contactState &&
      Number.isFinite(revision) &&
      revision > Number(contactState.booking_revision || 0)
    ) {
      contactState.booking_revision = revision;
    }
  });

  $("contactForm")?.addEventListener("submit", saveContact);
  $("backToPassengersButton")?.addEventListener(
    "click",
    () => openStep("passengers")
  );
  $("continueReviewButton")?.addEventListener(
    "click",
    () => openStep("review")
  );
  $("backToContactButton")?.addEventListener(
    "click",
    () => openStep("contact")
  );

  document
    .querySelector('[data-funnel-step="passengers"]')
    ?.addEventListener("click", () => openStep("passengers"));
  document
    .querySelector('[data-funnel-step="contact"]')
    ?.addEventListener("click", () => openStep("contact"));
  document
    .querySelector('[data-funnel-step="review"]')
    ?.addEventListener("click", () => openStep("review"));

  Promise.all([
    loadPassengerReadiness(),
    loadContact(),
  ]).then(() => {
    if (contactState) {
      renderContact(contactState);
    }
  });
})();
