(() => {
  "use strict";

  const $ = id => document.getElementById(id);

  const match = window.location.pathname.match(
    /\/app\/bookings\/([^/]+)\/pnr-workspace\/?$/
  );
  const bookingId = decodeURIComponent(match?.[1] || "");

  let booking = null;
  let workspace = null;
  let loading = false;

  function esc(value) {
    return String(value ?? "")
      .replaceAll("&", "&amp;")
      .replaceAll("<", "&lt;")
      .replaceAll(">", "&gt;")
      .replaceAll('"', "&quot;");
  }

  function text(id, value) {
    const node = $(id);
    if (node) node.textContent = value ?? "—";
  }

  function show(id, visible) {
    const node = $(id);
    if (node) node.classList.toggle("hidden", !visible);
  }

  function money(value, currency) {
    const amount = Number(value);
    if (!Number.isFinite(amount)) return "No disponible";
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
    const raw = String(value);
    const m = raw.match(
      /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/
    );
    if (!m) return raw;

    const [, , month, day, hour, minute] = m;
    const months = [
      "ene", "feb", "mar", "abr", "may", "jun",
      "jul", "ago", "sep", "oct", "nov", "dic",
    ];
    return `${day} ${months[Number(month) - 1] || month} · ${hour}:${minute}`;
  }

  function fmtRetrievedAt(value) {
    if (!value) return "Sin lectura válida";
    try {
      return new Intl.DateTimeFormat("es-AR", {
        dateStyle: "medium",
        timeStyle: "short",
      }).format(new Date(value));
    } catch {
      return String(value);
    }
  }

  function checkByCode(code) {
    return (workspace?.assessment?.checks || []).find(
      item => item.code === code
    ) || null;
  }

  function stateLabel(status) {
    return {
      ready_for_ticketing: "PNR verificado",
      needs_attention: "Requiere atención",
      read_error: "Verificación pendiente",
      verified: "Verificada",
      syncing: "Sincronizando",
    }[status] || status || "Reserva";
  }

  function stateClass(status) {
    if (status === "ready_for_ticketing" || status === "verified") {
      return "ready";
    }
    if (status === "needs_attention") return "attention";
    if (status === "read_error") return "error";
    return "loading";
  }

  function checkLabel(status) {
    return {
      pass: "Correcto",
      warn: "Atención",
      fail: "Revisar",
      unknown: "No verificado",
    }[status] || status || "—";
  }

  function setStateBadge(node, status) {
    if (!node) return;
    node.classList.remove("ready", "attention", "error", "loading");
    node.classList.add(stateClass(status));
    node.textContent = stateLabel(status);
  }

  function healthClass(status) {
    if (status === "pass") return "ok";
    if (status === "fail") return "fail";
    return "warn";
  }

  function setHealth(kind, check, fallback) {
    const card = document.querySelector(`[data-health="${kind}"]`);
    if (!card) return;

    card.classList.remove("ok", "warn", "fail");
    if (!check) {
      card.classList.add("warn");
      text(`health${kind[0].toUpperCase()}${kind.slice(1)}`, fallback);
      return;
    }

    card.classList.add(healthClass(check.status));
    text(
      `health${kind[0].toUpperCase()}${kind.slice(1)}`,
      checkLabel(check.status)
    );
  }

  function renderHero() {
    const locator = workspace?.confirmation_id || "—";
    text("headerLocator", `Reserva ${locator}`);
    text("workspaceTitle", `Reserva ${locator}`);

    setStateBadge($("workspaceStatus"), workspace?.status);
    setStateBadge($("heroStatus"), workspace?.status);

    const status = workspace?.status;
    const summary = {
      ready_for_ticketing:
        "La reserva está confirmada en Sabre y los controles bloqueantes están correctos.",
      needs_attention:
        "La reserva existe en Sabre, pero hay controles que requieren intervención antes de continuar.",
      read_error:
        "La reserva fue creada, pero no pudimos confirmar su estado actual en Sabre.",
      verified:
        "La reserva fue verificada correctamente en Sabre.",
    }[status] || "Estado actual de la reserva en Sabre.";

    text("workspaceSummary", summary);
    text(
      "syncState",
      `Última verificación: ${fmtRetrievedAt(workspace?.retrieved_at)}`
    );

    show("staleState", Boolean(workspace?.stale));
    show("readErrorBanner", status === "read_error");
    text(
      "readErrorMessage",
      workspace?.read_error_message ||
        "La reserva ya fue creada. Podés volver a intentar la sincronización."
    );
  }

  function renderHealth() {
    setHealth(
      "itinerary",
      checkByCode("SEGMENTS_CONFIRMED"),
      "No verificado"
    );
    setHealth(
      "passengers",
      checkByCode("PASSENGER_COUNT_MATCH"),
      "No verificado"
    );
    setHealth(
      "contact",
      checkByCode("CONTACT_PRESENT"),
      "No verificado"
    );
    setHealth(
      "pricing",
      checkByCode("PRICING_PRESENT"),
      "No verificado"
    );
  }

  function segmentStatusLabel(status) {
    const normalized = String(status || "").toUpperCase();
    if (normalized === "HK") return "HK · Confirmado";
    if (!normalized) return "Sin estado";
    return `${normalized} · Requiere revisión`;
  }

  function renderSegments() {
    const segments = workspace?.snapshot?.segments || [];
    text(
      "segmentCountBadge",
      `${segments.length} ${segments.length === 1 ? "segmento" : "segmentos"}`
    );

    $("pnrSegments").innerHTML = segments.length
      ? segments.map((segment, index) => {
          const confirmed = String(segment.status || "").toUpperCase() === "HK";
          return `
            <article class="pnr-segment ${confirmed ? "" : "segment-problem"}">
              <div class="pnr-segment-head">
                <span class="pnr-flight">
                  ${esc(segment.marketing_carrier || "—")}
                  ${esc(segment.flight_number || "—")}
                </span>
                <span class="segment-status ${confirmed ? "ok" : "fail"}">
                  ${esc(segmentStatusLabel(segment.status))}
                </span>
              </div>

              <div class="pnr-route">
                ${esc(segment.origin || "—")} → ${esc(segment.destination || "—")}
              </div>

              <div class="pnr-times">
                <div class="pnr-time">
                  <strong>${esc(fmtDateTime(segment.departure_at))}</strong>
                  <small>Salida · ${esc(segment.origin || "—")}</small>
                </div>
                <span class="pnr-time-arrow" aria-hidden="true">→</span>
                <div class="pnr-time">
                  <strong>${esc(fmtDateTime(segment.arrival_at))}</strong>
                  <small>Llegada · ${esc(segment.destination || "—")}</small>
                </div>
              </div>

              <div class="pnr-segment-meta">
                <span class="meta-chip">Clase ${esc(segment.booking_class || "—")}</span>
                <span class="meta-chip">Pax ${esc(segment.number_in_party ?? "—")}</span>
                ${
                  segment.operating_carrier &&
                  segment.operating_carrier !== segment.marketing_carrier
                    ? `<span class="meta-chip">Opera ${esc(segment.operating_carrier)}</span>`
                    : ""
                }
              </div>
            </article>
          `;
        }).join("")
      : `<div class="empty-note">Sabre no devolvió segmentos aéreos.</div>`;
  }

  function passengerName(passenger, index) {
    const surname = String(passenger?.surname || "").trim();
    const given = String(passenger?.given_name || "").trim();
    if (surname && given) return `${surname}, ${given}`;
    return surname || given || `Pasajero ${index + 1}`;
  }

  function renderPassengersAndContacts() {
    const passengers = workspace?.snapshot?.passengers || [];
    $("pnrPassengers").innerHTML = passengers.length
      ? passengers.map((passenger, index) => `
          <div class="data-item">
            <strong>${esc(passengerName(passenger, index))}</strong>
            <small>
              ${esc(passenger.passenger_type || "Tipo no informado")}
              · Name number ${esc(passenger.name_number || "—")}
            </small>
          </div>
        `).join("")
      : `<div class="empty-note">No se detectaron pasajeros.</div>`;

    const contacts = workspace?.snapshot?.contacts || [];
    $("pnrContacts").innerHTML = contacts.length
      ? contacts.map(contact => `
          <div class="data-item">
            <strong>${esc(contact.value || "—")}</strong>
            <small>
              ${contact.kind === "email" ? "Email" : "Teléfono"}
              ${contact.usage_type ? ` · ${esc(contact.usage_type)}` : ""}
            </small>
          </div>
        `).join("")
      : `<div class="empty-note">No se detectó contacto.</div>`;
  }

  function expectedBookingFare() {
    return booking?.accepted_offer_revision?.snapshot?.fare || null;
  }

  function renderPricing() {
    const quotes = workspace?.snapshot?.price_quotes || [];
    const fare = expectedBookingFare();

    text(
      "pricingBadge",
      quotes.length ? `${quotes.length} PQ` : "Sin PQ"
    );
    show("pricingEmpty", quotes.length === 0);
    show("expectedFare", quotes.length === 0 && Boolean(fare));

    if (quotes.length === 0 && fare) {
      $("expectedFare").innerHTML = `
        Selección congelada:
        <strong>
          ${esc(money(fare.total_price ?? fare.price_per_passenger, fare.currency))}
        </strong>
        ${fare.brand_name ? ` · ${esc(fare.brand_name)}` : ""}
      `;
    }

    $("pnrPricing").innerHTML = quotes.map((quote, index) => {
      const fareBasis = (quote.fare_basis_codes || []).length
        ? quote.fare_basis_codes.join(" / ")
        : (quote.fare_basis || "—");
      const classes = (quote.segment_booking_classes || []).join(" / ") || "—";

      return `
        <article class="pricing-card">
          <div class="pricing-card-head">
            <strong>PQ ${esc(quote.record_number || index + 1)}</strong>
            <span>
              ${esc(money(quote.total_amount, quote.total_currency))}
            </span>
          </div>
          <dl>
            <div>
              <dt>Estado</dt>
              <dd>
                ${esc(quote.status || "—")}
                ${quote.itinerary_changed === true ? " · ITIN CHG" : ""}
              </dd>
            </div>
            <div>
              <dt>Validating carrier</dt>
              <dd>${esc(quote.validating_carrier || "—")}</dd>
            </div>
            <div>
              <dt>Pasajero</dt>
              <dd>
                ${esc(quote.passenger_type || "—")}
                ${quote.passenger_quantity != null ? ` ×${esc(quote.passenger_quantity)}` : ""}
              </dd>
            </div>
            <div>
              <dt>Clases</dt>
              <dd>${esc(classes)}</dd>
            </div>
            <div>
              <dt>Fare basis</dt>
              <dd>${esc(fareBasis)}</dd>
            </div>
            <div>
              <dt>Guardada</dt>
              <dd>${esc(fmtRetrievedAt(quote.stored_at))}</dd>
            </div>
          </dl>
        </article>
      `;
    }).join("");
  }

  function assessmentCard(check) {
    return `
      <article class="assessment-item ${
        check.blocking && check.status === "fail" ? "blocking-fail" : ""
      }">
        <div class="assessment-item-main">
          <strong>${esc(check.label || check.code)}</strong>
          ${
            check.message
              ? `<small>${esc(check.message)}</small>`
              : ""
          }
        </div>

        <span class="check-status ${esc(check.status)}">
          ${esc(checkLabel(check.status))}
        </span>

        ${
          check.expected || check.actual
            ? `
              <div class="assessment-values">
                <span>Esperado: <strong>${esc(check.expected || "—")}</strong></span>
                <span>Sabre: <strong>${esc(check.actual || "—")}</strong></span>
              </div>
            `
            : ""
        }
      </article>
    `;
  }

  function renderChecks() {
    const checks = workspace?.assessment?.checks || [];
    const actionable = checks.filter(
      check => check.status !== "pass"
    );

    $("assessmentIssues").innerHTML = actionable.length
      ? actionable.map(assessmentCard).join("")
      : `
        <div class="message ok">
          No hay controles pendientes que requieran intervención.
        </div>
      `;

    $("assessmentChecks").innerHTML = checks.length
      ? checks.map(assessmentCard).join("")
      : `<div class="empty-note">Todavía no hay assessment disponible.</div>`;

    show("assessmentDetails", checks.length > actionable.length);
  }

  function nextActionDescription(code) {
    return {
      issue_ticket:
        "Los controles bloqueantes están correctos y existe un ticket candidate inequívoco. La emisión permanece deshabilitada.",
      store_or_verify_pricing:
        "El PNR no tiene una tarifa almacenada verificable. Primero hay que guardar o revisar el pricing.",
      review_itinerary:
        "Uno o más segmentos no coinciden o ya no están confirmados. Revisá el itinerario antes de cualquier otra acción.",
      review_passengers:
        "Los pasajeros requieren revisión antes de continuar.",
      review_contact:
        "El PNR no conserva contacto suficiente para continuar con seguridad.",
      review_pricing:
        "Existe pricing almacenado, pero alguno de sus datos no coincide con el Booking congelado.",
      reprice_required:
        "Sabre marca el PQ ACTIVE con ITIN CHG. Se requiere repricing antes de continuar.",
    }[code] || "Revisá los controles de la reserva antes de continuar.";
  }

  function renderNextAction() {
    if (workspace?.status === "read_error" && !workspace?.next_action) {
      text("nextActionTitle", "Actualizar desde Sabre");
      text(
        "nextActionDescription",
        "No hay una lectura válida suficiente para determinar el próximo paso."
      );
      show("nextActionReason", false);
      return;
    }

    const next = workspace?.next_action;
    const finalGateBlocked = (
      next?.code === "issue_ticket" &&
      workspace?.final_pre_issue_gate?.status === "blocked"
    );
    text(
      "nextActionTitle",
      finalGateBlocked
        ? "Revisión de ticketing requerida"
        : (
            next?.code === "issue_ticket"
              ? "Lista para revisión pre-emisión"
              : (next?.label || "Revisar reserva")
          )
    );
    text(
      "nextActionDescription",
      finalGateBlocked
        ? (
            workspace?.final_pre_issue_gate?.message ||
            "Los controles finales de ticketing requieren revisión."
          )
        : nextActionDescription(next?.code)
    );

    const blocking = (workspace?.assessment?.checks || []).filter(
      item => item.blocking && item.status === "fail"
    );

    if (blocking.length) {
      $("nextActionReason").innerHTML = `
        ${blocking.length === 1 ? "Control bloqueante" : "Controles bloqueantes"}:
        <strong>${blocking.map(item => esc(item.label || item.code)).join(", ")}</strong>
      `;
      show("nextActionReason", true);
    } else {
      show("nextActionReason", false);
    }
  }

  function renderTicketing() {
    const ticketing = workspace?.snapshot?.ticketing || {};

    const advisory = ticketing.advisory_present
      ? [
          ticketing.advisory_airline_code,
          ticketing.advisory_code,
          ticketing.advisory_status,
        ].filter(Boolean).join(":")
      : "Sin advisory detectado";

    text("ticketingAdvisory", advisory || "—");
    const purchaseDeadline = workspace?.purchase_deadline || null;
    text(
      "ticketingDeadline",
      purchaseDeadline?.operational_deadline_at ||
        purchaseDeadline?.purchase_deadline_at ||
        ticketing.deadline_at ||
        "No resuelto"
    );
    text(
      "ticketingType",
      ticketing.arrangement_raw ||
        ticketing.ticket_type ||
        "No informado"
    );

    const services = workspace?.snapshot?.special_services || [];
    const visibleServices = Array.from(
      new Map(
        services
          .filter(service => {
            const code = String(service.code || "").toUpperCase();
            if (code === "ADTK") return false;
            if (
              code === "OTHS" &&
              !service.status &&
              !(service.name_numbers || []).length &&
              !(service.segment_numbers || []).length
            ) {
              return false;
            }
            return Boolean(code);
          })
          .map(service => [
            [
              service.airline_code || "",
              service.code || "",
              service.status || "",
              ...(service.name_numbers || []),
              ...(service.segment_numbers || []),
            ].join("|"),
            service,
          ])
      ).values()
    );

    $("specialServices").innerHTML = visibleServices.length
      ? visibleServices.map(service => `
          <span class="ssr-chip">
            ${esc(service.airline_code || "—")}:
            ${esc(service.code || "—")}:
            ${esc(service.status || "—")}
          </span>
        `).join("")
      : `<div class="empty-note">No hay SSR adicionales relevantes.</div>`;
  }


  function combinedCheckStatus(codes) {
    const checks = codes.map(checkByCode).filter(Boolean);
    if (checks.length !== codes.length) return "unknown";
    if (checks.some(check => check.status === "fail")) return "fail";
    if (checks.every(check => check.status === "pass")) return "pass";
    return "unknown";
  }

  function preIssueItem(label, status, detail) {
    const symbol = status === "pass" ? "✓" : status === "fail" ? "!" : "·";
    return `
      <article class="pre-issue-check ${esc(status)}">
        <span class="pre-issue-check-icon" aria-hidden="true">
          ${esc(symbol)}
        </span>
        <div>
          <strong>${esc(label)}</strong>
          <small>${esc(detail)}</small>
        </div>
      </article>
    `;
  }

  function candidatePassengerName(candidatePassenger, index) {
    const nameNumber = String(candidatePassenger?.name_number || "");
    const passenger = (workspace?.snapshot?.passengers || []).find(
      item => String(item.name_number || "") === nameNumber
    );
    return passenger
      ? passengerName(passenger, index)
      : `Passenger ${nameNumber || index + 1}`;
  }

  function renderPreIssueReview() {
    const readiness = workspace?.pre_issue_readiness || null;
    const candidate = workspace?.ticket_candidate || null;
    const constraint = workspace?.ticketing_constraint || null;
    const purchaseDeadline = workspace?.purchase_deadline || null;
    const finalGate = workspace?.final_pre_issue_gate || null;

    const ready = finalGate?.status === "ready";

    const badge = $("preIssueBadge");
    if (badge) {
      badge.classList.remove("ready", "attention", "error", "loading");
      badge.classList.add(ready ? "ready" : "attention");
      badge.textContent = ready
        ? "READY FOR PRE-ISSUE"
        : "BLOCKED";
    }

    text(
      "preIssueSummary",
      ready
        ? (
            finalGate?.message ||
            "Todos los gates read-only están completos."
          )
        : (
            finalGate?.message ||
            readiness?.message ||
            "El PNR todavía no cumple todos los gates finales."
          )
    );

    const freshStatus = (
      readiness?.fresh_remote_read === true &&
      workspace?.stale !== true &&
      workspace?.status !== "read_error"
    ) ? "pass" : "fail";

    const finalBlockers = finalGate?.blockers || [];
    const deadlineStatus = ready
      ? "pass"
      : (
          finalBlockers.some(item => [
            "PURCHASE_DEADLINE_EXPIRED",
            "PURCHASE_DEADLINE_MISSING",
            "PURCHASE_DEADLINE_TIME_MISSING",
            "PURCHASE_DEADLINE_YEAR_UNRESOLVED",
            "PURCHASE_DEADLINE_FORMAT_UNSUPPORTED",
            "PURCHASE_DEADLINE_UNRESOLVED",
            "ACTIVE_PQ_UNAVAILABLE",
            "TICKETING_DEADLINE_UNRESOLVED",
            "TICKETING_DEADLINE_EXPIRED",
            "TICKETING_DEADLINE_TIMEZONE_UNKNOWN",
            "TICKETING_CONSTRAINT_UNAVAILABLE",
          ].includes(item))
            ? "fail"
            : "unknown"
        );

    let deadlineDetail = "Restricción de ticketing no disponible.";
    if (purchaseDeadline?.status === "expired") {
      deadlineDetail = purchaseDeadline?.purchase_deadline_at
        ? `LAST DAY TO PURCHASE vencido: ${purchaseDeadline.purchase_deadline_at}`
        : "El LAST DAY TO PURCHASE del PQ ACTIVE ya venció.";
    } else if (purchaseDeadline?.status === "resolved") {
      const source = purchaseDeadline?.raw_values?.join(" · ") || "PQ ACTIVE";
      const operational = purchaseDeadline?.operational_deadline_at || "—";
      deadlineDetail = purchaseDeadline?.policy_capped
        ? `${source}. Time limit operativo: ${operational} (cap mañana 12:00 Buenos Aires).`
        : `${source}. Time limit operativo: ${operational}.`;
    } else if (purchaseDeadline?.status === "unresolved") {
      deadlineDetail = (
        purchaseDeadline?.message ||
        "No se pudo resolver LAST DAY TO PURCHASE en todos los PQ ACTIVE."
      );
    } else if (constraint?.status === "structured_deadline") {
      deadlineDetail = constraint?.deadline_at
        ? `Deadline estructurado: ${constraint.deadline_at}`
        : "Deadline estructurado sin valor utilizable.";
    } else if (constraint?.status === "advisory_without_deadline") {
      const advisory = [
        constraint?.advisory_airline_code,
        constraint?.advisory_code,
        constraint?.advisory_status,
      ].filter(Boolean).join(":");
      deadlineDetail = `${advisory || "ADTK"} sin deadline estructurado; requiere verificación.`;
    } else if (constraint?.status === "no_structured_constraint") {
      deadlineDetail = "No hay deadline estructurado; esto no equivale a ausencia de vencimiento.";
    } else if (constraint?.status === "unverified_deadline") {
      deadlineDetail = "El deadline recibido no puede interpretarse con seguridad.";
    }

    const checks = [
      [
        "Lectura Sabre actual",
        freshStatus,
        freshStatus === "pass"
          ? `Verificada ${fmtRetrievedAt(readiness?.retrieved_at || workspace?.retrieved_at)}`
          : "Se requiere una nueva lectura remota exitosa.",
      ],
      [
        "Itinerario confirmado",
        combinedCheckStatus(["SEGMENTS_MATCH", "SEGMENTS_CONFIRMED"]),
        "Segmentos exactos contra el Booking y estado HK.",
      ],
      [
        "PQ vigente para itinerario",
        checkByCode("PRICING_ITINERARY_CURRENT")?.status || "unknown",
        checkByCode("PRICING_ITINERARY_CURRENT")?.status === "pass"
          ? "El PQ ACTIVE no está marcado con ITIN CHG."
          : "ITIN CHG o estado no verificable: se requiere repricing.",
      ],
      [
        "Pasajeros y pricing",
        combinedCheckStatus([
          "PASSENGER_COUNT_MATCH",
          "PASSENGER_TYPES_MATCH",
          "PRICING_PASSENGER_COVERAGE",
        ]),
        "Todos los pasajeros deben quedar cubiertos exactamente una vez.",
      ],
      [
        "PQ ACTIVE",
        checkByCode("ACTIVE_PRICING_SELECTED")?.status || "unknown",
        "Sólo pricing con status Sabre ACTIVE entra al candidato.",
      ],
      [
        "Tarifa y carrier",
        combinedCheckStatus([
          "CURRENCY_MATCH",
          "PRICE_MATCH",
          "VALIDATING_CARRIER_MATCH",
        ]),
        "Moneda, total y validating carrier contra el Booking congelado.",
      ],
      [
        "Ticketing deadline",
        deadlineStatus,
        deadlineDetail,
      ],
      [
        "Ticket Candidate",
        (
          candidate?.status === "ready" &&
          checkByCode("TICKET_CANDIDATE_READY")?.status === "pass"
        ) ? "pass" : (
          candidate?.status === "blocked" ? "fail" : "unknown"
        ),
        candidate?.status === "ready"
          ? "Conjunto de PQ y pasajeros inequívoco."
          : "No hay un ticket candidate inequívoco.",
      ],
    ];

    $("preIssueChecks").innerHTML = checks
      .map(([label, status, detail]) => preIssueItem(label, status, detail))
      .join("");

    text(
      "ticketCandidateBadge",
      candidate?.status === "ready" ? "READY" : "BLOCKED"
    );
    text("candidateLocator", candidate?.confirmation_id || "—");
    text(
      "candidatePqRecords",
      (candidate?.price_quote_record_numbers || []).length
        ? candidate.price_quote_record_numbers.join(", ")
        : "—"
    );
    text("candidateCarrier", candidate?.validating_carrier || "—");
    text(
      "candidateTotal",
      candidate?.total_amount != null
        ? money(candidate.total_amount, candidate.currency)
        : "—"
    );

    const candidatePassengers = candidate?.passengers || [];
    $("ticketCandidatePassengers").innerHTML = candidatePassengers.length
      ? candidatePassengers.map((passenger, index) => `
          <div class="pre-issue-passenger">
            <div>
              <strong>${esc(candidatePassengerName(passenger, index))}</strong>
              <small>
                ${esc(passenger.name_number || "—")}
                · ${esc(passenger.passenger_type || "—")}
              </small>
            </div>
            <span class="meta-chip">
              PQ ${esc(passenger.price_quote_record_number || "—")}
            </span>
          </div>
        `).join("")
      : `<div class="empty-note">No hay pasajeros en el ticket candidate.</div>`;

    const blockers = Array.from(new Set([
      ...(finalGate?.blockers || []),
      ...(readiness?.blockers || []),
      ...(candidate?.blockers || []),
    ]));
    if (blockers.length) {
      $("preIssueBlockers").innerHTML = `
        <strong>Bloqueos detectados</strong>
        <ul>
          ${blockers.map(item => `<li>${esc(item)}</li>`).join("")}
        </ul>
      `;
      show("preIssueBlockers", true);
    } else {
      $("preIssueBlockers").innerHTML = "";
      show("preIssueBlockers", false);
    }

    const issueButton = $("issueTicketButton");
    if (issueButton) {
      issueButton.disabled = true;
      issueButton.setAttribute("aria-disabled", "true");
    }
  }

  function renderTechnical() {
    text("technicalBookingId", workspace?.booking_id || bookingId);
    text("technicalLocator", workspace?.confirmation_id || "—");
    text(
      "technicalEnvironment",
      String(workspace?.environment || "").toUpperCase() || "—"
    );
    text("technicalProvider", workspace?.provider || "—");
    text(
      "technicalRetrievedAt",
      fmtRetrievedAt(workspace?.retrieved_at)
    );

    const quoteId = booking?.source_quote_id;
    const link = $("sourceQuoteLink");
    if (quoteId) {
      link.textContent = `Volver a ${quoteId}`;
      link.href = `/app?quote=${encodeURIComponent(quoteId)}`;
    } else {
      link.textContent = "Volver a cotizaciones";
      link.href = "/app";
    }
  }

  function renderWorkspace() {
    renderHero();

    if (!workspace?.snapshot) {
      show("healthSummary", false);
      show("workspaceContent", false);
      return;
    }

    show("healthSummary", true);
    show("workspaceContent", true);

    renderHealth();
    renderSegments();
    renderPassengersAndContacts();
    renderPricing();
    renderChecks();
    renderNextAction();
    renderTicketing();
    renderPreIssueReview();
    renderTechnical();
  }

  async function fetchJson(url) {
    const response = await fetch(url, {
      method: "GET",
      headers: { Accept: "application/json" },
      cache: "no-store",
    });

    let data = null;
    try {
      data = await response.json();
    } catch {
      data = null;
    }

    if (!response.ok) {
      throw new Error(
        data?.detail || `No se pudo cargar la reserva (HTTP ${response.status}).`
      );
    }
    return data;
  }

  async function loadBooking() {
    try {
      booking = await fetchJson(
        `/bookings/${encodeURIComponent(bookingId)}`
      );
    } catch {
      // Booking metadata enriches the UI, but a real PNR Workspace response
      // remains useful even if this secondary read fails.
      booking = null;
    }
  }

  async function syncWorkspace() {
    if (loading) return;
    loading = true;

    const button = $("refreshWorkspaceButton");
    if (button) {
      button.disabled = true;
      button.textContent = "Sincronizando…";
    }

    show("workspaceError", false);

    try {
      workspace = await fetchJson(
        `/bookings/${encodeURIComponent(bookingId)}/pnr-workspace`
      );
      renderWorkspace();
      show("workspaceLoading", false);
      show("pnrWorkspace", true);
    } catch (error) {
      $("workspaceError").textContent = error.message || String(error);
      show("workspaceError", true);
      show("workspaceLoading", false);

      if (!workspace) {
        show("pnrWorkspace", false);
      }
    } finally {
      loading = false;
      if (button) {
        button.disabled = false;
        button.textContent = "Actualizar desde Sabre";
      }
    }
  }

  async function init() {
    if (!bookingId) {
      $("workspaceError").textContent =
        "No se pudo determinar el Booking desde la URL.";
      show("workspaceLoading", false);
      show("workspaceError", true);
      return;
    }

    await loadBooking();
    await syncWorkspace();
  }

  $("refreshWorkspaceButton")?.addEventListener(
    "click",
    syncWorkspace
  );

  init();
})();
