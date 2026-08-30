(() => {
  "use strict";

  const bookingId = decodeURIComponent(
    window.location.pathname.split("/").filter(Boolean).pop() || ""
  );
  const $ = id => document.getElementById(id);

  let passengerState = null;

  function esc(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function passengerTypeLabel(passenger) {
    const type = String(passenger?.passenger_type || "");
    if (type === "ADT") return "Adulto";
    if (type === "INF") return "Infante";
    if (type === "CHILD") {
      return passenger.quoted_age != null
        ? `Niño · ${passenger.quoted_age} años cotizados`
        : "Niño";
    }
    return type || "Pasajero";
  }

  function setStatus(message, kind = "info") {
    const node = $("passengerLoadStatus");
    if (!node) return;

    if (!message) {
      node.textContent = "";
      node.className = "message hidden";
      return;
    }

    node.textContent = message;
    node.className = `message ${kind}`;
  }

  function adultOptions(passengers, selected) {
    return passengers
      .filter(item => item.passenger_type === "ADT")
      .map(item => `
        <option
          value="${item.slot_index}"
          ${Number(selected) === Number(item.slot_index) ? "selected" : ""}
        >
          Pasajero ${item.slot_index} · Adulto
        </option>
      `)
      .join("");
  }

  function renderInfantAssociation(passenger, passengers) {
    if (passenger.passenger_type !== "INF") return "";

    const adults = passengers.filter(
      item => item.passenger_type === "ADT"
    );

    if (adults.length <= 1) {
      const adult = adults[0];
      return `
        <div class="field full-width immutable-field">
          <span>Adulto asociado</span>
          <strong>
            ${adult ? `Pasajero ${adult.slot_index} · Adulto` : "Sin adulto disponible"}
          </strong>
          <small>Asociación automática según el mix tarifado.</small>
        </div>
      `;
    }

    return `
      <label class="field full-width">
        <span>Adulto asociado *</span>
        <select
          data-field="associated_adult_slot_index"
          data-slot="${passenger.slot_index}"
          required
        >
          <option value="">Seleccionar adulto</option>
          ${adultOptions(
            passengers,
            passenger.associated_adult_slot_index
          )}
        </select>
      </label>
    `;
  }

  function renderPassenger(passenger, passengers) {
    const title = passengerTypeLabel(passenger);
    const immutable = [
      `Tipo tarifado: ${passenger.passenger_type}`,
      passenger.quoted_age != null
        ? `Edad cotizada: ${passenger.quoted_age}`
        : null,
    ].filter(Boolean).join(" · ");

    return `
      <section
        class="passenger-card ${passenger.complete ? "complete" : ""}"
        data-passenger-slot="${passenger.slot_index}"
      >
        <div class="passenger-card-head">
          <div>
            <span class="passenger-number">
              Pasajero ${passenger.slot_index}
            </span>
            <h3>${esc(title)}</h3>
            <small>${esc(immutable)}</small>
          </div>
          <span class="passenger-card-status">
            ${passenger.complete ? "Completo" : "Pendiente"}
          </span>
        </div>

        <div class="passenger-grid">
          <label class="field">
            <span>Nombre *</span>
            <input
              type="text"
              maxlength="60"
              data-field="given_name"
              data-slot="${passenger.slot_index}"
              value="${esc(passenger.given_name || "")}"
              required
            >
          </label>

          <label class="field">
            <span>Segundo nombre</span>
            <input
              type="text"
              maxlength="60"
              data-field="middle_name"
              data-slot="${passenger.slot_index}"
              value="${esc(passenger.middle_name || "")}"
            >
          </label>

          <label class="field">
            <span>Apellido *</span>
            <input
              type="text"
              maxlength="60"
              data-field="surname"
              data-slot="${passenger.slot_index}"
              value="${esc(passenger.surname || "")}"
              required
            >
          </label>

          <label class="field">
            <span>Fecha de nacimiento *</span>
            <input
              type="date"
              data-field="date_of_birth"
              data-slot="${passenger.slot_index}"
              value="${esc(passenger.date_of_birth || "")}"
              required
            >
          </label>

          <label class="field">
            <span>Género *</span>
            <select
              data-field="gender"
              data-slot="${passenger.slot_index}"
              required
            >
              <option value="">Seleccionar</option>
              <option value="M" ${passenger.gender === "M" ? "selected" : ""}>
                Masculino
              </option>
              <option value="F" ${passenger.gender === "F" ? "selected" : ""}>
                Femenino
              </option>
              <option value="X" ${passenger.gender === "X" ? "selected" : ""}>
                X / no especificado
              </option>
            </select>
          </label>

          ${renderInfantAssociation(passenger, passengers)}
        </div>
      </section>
    `;
  }

  function renderPassengers(data) {
    passengerState = data;

    const fields = $("passengerFields");
    if (!fields) return;

    fields.innerHTML = (data.passengers || [])
      .map(item => renderPassenger(item, data.passengers || []))
      .join("");

    const badge = $("passengerCompletionBadge");
    if (badge) {
      badge.textContent = data.complete
        ? "Pasajeros completos"
        : "Datos pendientes";
      badge.classList.toggle("complete-badge", Boolean(data.complete));
    }

    const continueButton = $("continueContactButton");
    if (continueButton) {
      continueButton.disabled = !data.complete;
    }

    window.dispatchEvent(
      new CustomEvent("booking:passengers-state", {detail: data})
    );
    window.dispatchEvent(
      new CustomEvent("booking:revision-changed", {
        detail: {booking_revision: data.booking_revision},
      })
    );
  }

  function fieldValue(slot, field) {
    const selector =
      `[data-slot="${slot}"][data-field="${field}"]`;
    const node = document.querySelector(selector);
    if (!node) return null;

    const value = String(node.value || "").trim();
    return value || null;
  }

  function collectPassengerPayload() {
    return (passengerState?.passengers || []).map(passenger => ({
      slot_index: passenger.slot_index,
      given_name: fieldValue(passenger.slot_index, "given_name"),
      middle_name: fieldValue(passenger.slot_index, "middle_name"),
      surname: fieldValue(passenger.slot_index, "surname"),
      date_of_birth: fieldValue(
        passenger.slot_index,
        "date_of_birth"
      ),
      gender: fieldValue(passenger.slot_index, "gender"),
      associated_adult_slot_index:
        passenger.passenger_type === "INF"
          ? (
              Number(
                fieldValue(
                  passenger.slot_index,
                  "associated_adult_slot_index"
                )
              ) ||
              passenger.associated_adult_slot_index ||
              null
            )
          : null,
    }));
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

  async function loadPassengers() {
    if (!bookingId) return;

    setStatus("Cargando pasajeros…", "info");

    try {
      const data = await api(
        `/bookings/${encodeURIComponent(bookingId)}/passengers`
      );
      renderPassengers(data);
      setStatus("");
    } catch (error) {
      setStatus(
        error.message || String(error),
        "error"
      );
    }
  }

  async function savePassengers(event) {
    event.preventDefault();

    const form = $("passengerForm");
    if (!form || !passengerState) return;
    if (!form.reportValidity()) return;

    const button = $("savePassengersButton");
    if (button) {
      button.disabled = true;
      button.textContent = "Guardando…";
    }
    setStatus("Guardando identidades…", "info");

    try {
      const passengers = collectPassengerPayload();
      const data = await api(
        `/bookings/${encodeURIComponent(bookingId)}/passengers`,
        {
          method: "PUT",
          headers: {"Content-Type": "application/json"},
          body: JSON.stringify({
            revision: passengerState.booking_revision,
            passengers,
          }),
        }
      );

      renderPassengers(data);
      setStatus(
        data.complete
          ? "Pasajeros guardados ✓"
          : "Datos guardados. Todavía faltan pasajeros por completar.",
        data.complete ? "ok" : "info"
      );
    } catch (error) {
      setStatus(
        error.message || String(error),
        "error"
      );

      if (
        String(error.message || "").includes("Recargá") ||
        String(error.message || "").includes("modificado en paralelo")
      ) {
        await loadPassengers();
      }
    } finally {
      if (button) {
        button.disabled = false;
        button.textContent = "Guardar pasajeros";
      }
    }
  }

  function continueToContact() {
    if (!passengerState?.complete) return;

    if (window.bookingFunnel?.openStep) {
      window.bookingFunnel.openStep("contact");
    }
  }

  window.addEventListener("booking:revision-changed", event => {
    const revision = Number(event.detail?.booking_revision);
    if (
      passengerState &&
      Number.isFinite(revision) &&
      revision > Number(passengerState.booking_revision || 0)
    ) {
      passengerState.booking_revision = revision;
    }
  });

  $("passengerForm")?.addEventListener("submit", savePassengers);
  $("continueContactButton")?.addEventListener(
    "click",
    continueToContact
  );

  loadPassengers();
})();
