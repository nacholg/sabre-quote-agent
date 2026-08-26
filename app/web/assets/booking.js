(() => {
  "use strict";

  const bookingId = decodeURIComponent(
    window.location.pathname.split("/").filter(Boolean).pop() || ""
  );

  const $ = id => document.getElementById(id);

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

  function fmtDateTime(value) {
    if (!value) return "—";

    // Sabre segment timestamps represent local airport wall-clock time.
    // Do not convert them to the browser timezone (for example MIA -> EZE),
    // otherwise arrival/departure times change on screen.
    const raw = String(value);
    const match = raw.match(
      /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/
    );
    if (!match) return raw;

    const [, , month, day, hour, minute] = match;
    const months = [
      "ene", "feb", "mar", "abr", "may", "jun",
      "jul", "ago", "sep", "oct", "nov", "dic",
    ];
    const monthLabel = months[Number(month) - 1] || month;

    return `${day}-${monthLabel}, ${hour}:${minute}`;
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

  function passengerLabel(item) {
    const type = String(item?.type || "");
    if (type === "ADT") return "Adulto";
    if (type === "INF") return "Infante";
    if (type === "CHILD") {
      return item.age != null ? `Niño · ${item.age} años` : "Niño";
    }
    return type || "Pasajero";
  }

  function renderSegments(segments) {
    $("segments").innerHTML = (segments || []).map(segment => `
      <div class="segment">
        <div class="segment-head">
          <span>${esc(segment.marketing_carrier)} ${esc(segment.flight_number)}</span>
          <span>${esc(segment.booking_class || "—")}</span>
        </div>
        <div class="segment-route">
          ${esc(segment.departure_airport)} → ${esc(segment.arrival_airport)}
        </div>
        <div class="segment-time">
          ${esc(fmtDateTime(segment.departure_at))}
          &nbsp;→&nbsp;
          ${esc(fmtDateTime(segment.arrival_at))}
        </div>
      </div>
    `).join("");
  }

  function renderPassengers(passengers) {
    $("passengerMix").innerHTML = (passengers || []).map(item => `
      <div class="passenger-chip">
        <strong>${esc(passengerLabel(item))} ×${esc(item.quantity || 1)}</strong>
        <small>Tipo tarifado: ${esc(item.type)}</small>
      </div>
    `).join("");
  }

  async function loadBooking() {
    $("bookingId").textContent = bookingId || "Booking";

    try {
      const response = await fetch(`/bookings/${encodeURIComponent(bookingId)}`);
      const data = await response.json();
      if (!response.ok) {
        throw new Error(data?.detail || `HTTP ${response.status}`);
      }

      const revision = data.accepted_offer_revision;
      const snapshot = revision?.snapshot;
      const fare = snapshot?.fare || {};

      if (!revision || !snapshot) {
        throw new Error("La reserva no tiene una revisión de oferta aceptada.");
      }

      $("bookingStatus").textContent = statusLabel(data.status);
      $("handoffBookingId").textContent = data.booking_id;
      $("environment").textContent = String(data.environment || "").toUpperCase();
      $("offerRevision").textContent = `#${revision.revision_number}`;
      $("revisionNumber").textContent = `#${revision.revision_number}`;

      const sourceUrl = `/app?quote=${encodeURIComponent(data.source_quote_id)}`;
      $("sourceQuoteLink").textContent = data.source_quote_id;
      $("sourceQuoteLink").href = sourceUrl;
      $("backToQuote").href = sourceUrl;

      $("fareTitle").textContent =
        fare.brand_name || fare.brand_code || fare.cabin || "Tarifa";
      $("totalPrice").textContent = money(
        fare.total_price ?? fare.price_per_passenger,
        fare.currency
      );
      $("cabin").textContent = fare.cabin || "—";
      $("validatingCarrier").textContent = fare.validating_carrier || "—";
      $("fareBasis").textContent =
        Array.isArray(fare.fare_basis_codes) && fare.fare_basis_codes.length
          ? fare.fare_basis_codes.join(" / ")
          : "No informado";

      renderSegments(snapshot.segments);
      renderPassengers(snapshot.passenger_mix);

      $("bookingWorkspace").classList.remove("hidden");
    } catch (error) {
      $("bookingError").textContent = error.message || String(error);
      $("bookingError").classList.remove("hidden");
    }
  }

  loadBooking();
})();
