(() => {
  "use strict";

  function installStylesheet() {
    if (document.querySelector('link[data-booking-handoff="1"]')) return;
    const link = document.createElement("link");
    link.rel = "stylesheet";
    link.href = "/app/assets/booking-handoff.css";
    link.dataset.bookingHandoff = "1";
    document.head.appendChild(link);
  }

  function persistedFareSelection(rank) {
    const selectedRanks = Array.isArray(currentQuote?.selected_ranks)
      ? currentQuote.selected_ranks.map(Number)
      : [];
    if (!selectedRanks.includes(Number(rank))) return null;

    return (currentQuote?.selected_fares || []).find(
      item => Number(item?.rank) === Number(rank)
    ) || null;
  }

  function shownFareIndex(rank) {
    const value = selectedFareIndexByRank.get(Number(rank));
    return Number.isInteger(value) ? value : null;
  }

  function requestStorageKey(quoteId, rank, fareIndex) {
    return `booking-reserve:${quoteId}:${rank}:${fareIndex}`;
  }

  function reserveRequestId(quoteId, rank, fareIndex) {
    const key = requestStorageKey(quoteId, rank, fareIndex);
    let requestId = sessionStorage.getItem(key);
    if (!requestId) {
      requestId = crypto.randomUUID();
      sessionStorage.setItem(key, requestId);
    }
    return requestId;
  }

  async function startBooking(rank, button) {
    const selection = persistedFareSelection(rank);
    const fareIndex = shownFareIndex(rank);

    if (
      !currentQuoteId ||
      !selection ||
      fareIndex === null ||
      Number(selection.fare_index) !== fareIndex
    ) {
      alert("Guardá primero la selección y la tarifa exacta que querés reservar.");
      renderReserveActions();
      return;
    }

    const original = button.textContent;
    button.disabled = true;
    button.textContent = "Creando reserva…";

    try {
      const booking = await api(`/quotes/${currentQuoteId}/bookings`, {
        method: "POST",
        headers: {"Content-Type": "application/json"},
        body: JSON.stringify({
          rank: Number(rank),
          client_request_id: reserveRequestId(
            currentQuoteId,
            Number(rank),
            fareIndex
          ),
        }),
      });

      window.location.assign(
        `/app/bookings/${encodeURIComponent(booking.booking_id)}`
      );
    } catch (error) {
      button.disabled = false;
      button.textContent = original;
      alert(error.message || String(error));
    }
  }

  function renderReserveActions() {
    document.querySelectorAll(".option-card[data-option-rank]").forEach(card => {
      const rank = Number(card.dataset.optionRank);
      const selection = persistedFareSelection(rank);
      const fareIndex = shownFareIndex(rank);
      const exactPersisted = Boolean(
        selection &&
        fareIndex !== null &&
        Number(selection.fare_index) === fareIndex
      );

      let slot = card.querySelector(".booking-reserve-slot");
      if (!slot) {
        slot = document.createElement("div");
        slot.className = "booking-reserve-slot";
        const priceCell = card.querySelector(".result-row-grid .result-cell:last-child");
        priceCell?.appendChild(slot);
      }
      if (!slot) return;

      const historical = Boolean(
        currentVersionHistory && !currentVersionHistory.is_latest
      );

      if (!exactPersisted || historical) {
        slot.innerHTML = "";
        slot.classList.add("hidden");
        return;
      }

      slot.classList.remove("hidden");
      slot.innerHTML = `
        <button
          class="booking-reserve-button"
          type="button"
          data-current-only="true"
          data-booking-rank="${rank}"
        >Reservar</button>
      `;
      slot.querySelector("button")?.addEventListener(
        "click",
        event => startBooking(rank, event.currentTarget)
      );
    });
  }

  function wrapFunction(name, after) {
    const original = globalThis[name];
    if (typeof original !== "function") return;

    globalThis[name] = async function(...args) {
      const result = await original.apply(this, args);
      after();
      return result;
    };
  }

  function wrapSyncFunction(name, after) {
    const original = globalThis[name];
    if (typeof original !== "function") return;

    globalThis[name] = function(...args) {
      const result = original.apply(this, args);
      queueMicrotask(after);
      return result;
    };
  }

  function installHooks() {
    wrapSyncFunction("renderQuote", renderReserveActions);
    wrapSyncFunction("selectFareForResult", renderReserveActions);
    wrapFunction("saveSelection", renderReserveActions);
    wrapFunction("clearSelection", renderReserveActions);
  }

  function openRequestedQuote() {
    const quoteId = new URLSearchParams(window.location.search).get("quote");
    if (quoteId && typeof openQuote === "function") {
      openQuote(quoteId);
    }
  }

  installStylesheet();
  installHooks();

  window.addEventListener("DOMContentLoaded", () => {
    renderReserveActions();
    openRequestedQuote();
  });
})();
