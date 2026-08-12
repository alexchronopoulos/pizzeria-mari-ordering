(() => {
  const data = window.GIFT_CARD_DATA;
  const csrf = document.querySelector('meta[name="csrf-token"]')?.content;
  const giftStep = document.querySelector('#gift-card-step');
  const cardStep = document.querySelector('#remainder-card-step');
  const giftButton = document.querySelector('#gift-card-button');
  const cardButton = document.querySelector('#remainder-card-button');
  const exitLink = document.querySelector('#gift-card-exit');
  const status = document.querySelector('#payment-status');
  const errorBox = document.querySelector('#payment-error');
  let giftCard;
  let card;
  let statusCheckInFlight = false;
  let statusWatch = null;

  const money = (cents) => `$${(cents / 100).toFixed(2)}`;

  const showError = (message) => {
    status.textContent = '';
    errorBox.textContent = message;
    errorBox.hidden = false;
  };

  const clearError = () => {
    errorBox.textContent = '';
    errorBox.hidden = true;
  };

  const secureExit = () => {
    if (!exitLink) return;
    exitLink.href = data.confirmationUrl;
    exitLink.textContent = 'Check payment status';
  };

  const restoreCheckoutExit = () => {
    if (!exitLink) return;
    exitLink.href = data.checkoutUrl;
    exitLink.textContent = 'Return to checkout';
  };

  const tokenize = async (paymentMethod, verificationDetails) => {
    const result = verificationDetails
      ? await paymentMethod.tokenize(verificationDetails)
      : await paymentMethod.tokenize();
    if (result.status === 'OK' && result.token) return result.token;
    const detail = (result.errors || []).map((item) => item.message).filter(Boolean).join(' ');
    throw new Error(detail || 'Square could not verify those payment details.');
  };

  const submitToken = async (sourceId, paymentMethod) => {
    const response = await fetch('/api/gift-card/payment', {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-CSRF-Token': csrf,
      },
      body: JSON.stringify({
        attempt_id: data.attemptId,
        payment_method: paymentMethod,
        source_id: sourceId,
      }),
    });
    let payload = {};
    try {
      payload = await response.json();
    } catch (_error) {
      // An invalid response after a payment token was submitted is ambiguous.
    }
    if (!response.ok) {
      const error = new Error(payload.error || 'Square could not process that payment.');
      error.ambiguous = response.status >= 500;
      throw error;
    }
    return payload;
  };

  const updateBalance = (payload) => {
    data.paidCents = payload.paid_cents ?? data.paidCents;
    data.remainingCents = payload.remaining_cents;
    document.querySelector('#gift-applied-label').hidden = false;
    document.querySelector('#gift-applied-total').hidden = false;
    document.querySelector('#gift-applied-total').textContent = money(data.paidCents);
    document.querySelector('#gift-remaining-total').textContent = money(data.remainingCents);
    cardButton.textContent = `Pay remaining ${money(data.remainingCents)}`;
    secureExit();
  };

  const initializeCard = async (payments) => {
    if (card) return card;
    card = await payments.card();
    await card.attach('#card-container');
    return card;
  };

  const checkExistingPayment = async () => {
    if (statusCheckInFlight) return null;
    statusCheckInFlight = true;
    try {
      const response = await fetch(data.statusUrl, {
        headers: { Accept: 'application/json' },
        cache: 'no-store',
      });
      const payload = await response.json();
      if (!response.ok) return null;
      return payload;
    } catch (_error) {
      return null;
    } finally {
      statusCheckInFlight = false;
    }
  };

  const applyRecoveredState = async (payload, payments, phase) => {
    if (!payload) return false;
    if (payload.status === 'COMPLETED') {
      window.location.assign(payload.redirect_url || data.confirmationUrl);
      return true;
    }
    if (phase === 'gift_card' && payload.status === 'PARTIAL') {
      updateBalance(payload);
      data.giftCardApplied = true;
      giftStep.hidden = true;
      cardStep.hidden = false;
      status.textContent = `Gift card applied. Enter a card for the remaining ${money(payload.remaining_cents)}.`;
      await initializeCard(payments);
      return true;
    }
    return false;
  };

  const stopStatusWatch = () => {
    if (statusWatch) window.clearInterval(statusWatch);
    statusWatch = null;
  };

  const startStatusWatch = (payments, phase) => {
    stopStatusWatch();
    const startedAt = Date.now();
    const poll = async () => {
      const payload = await checkExistingPayment();
      if (await applyRecoveredState(payload, payments, phase)) {
        stopStatusWatch();
        return;
      }
      if (Date.now() - startedAt >= 45000) {
        stopStatusWatch();
        status.textContent = 'We are still checking Square. Do not submit another order.';
        secureExit();
      }
    };
    window.setTimeout(poll, 1500);
    statusWatch = window.setInterval(poll, 3000);
  };

  const uncertainPayment = () => {
    secureExit();
    showError(
      'We could not confirm Square’s response. Your payment may have gone through. '
      + 'Do not submit another order; use “Check payment status” below.',
    );
  };

  const initialize = async () => {
    if (!window.Square) {
      showError('Square payment fields could not load. Refresh the page and try again.');
      return;
    }
    let payments;
    try {
      payments = window.Square.payments(data.applicationId, data.locationId);
      if (data.giftCardApplied) {
        secureExit();
        await initializeCard(payments);
      } else {
        giftCard = await payments.giftCard();
        await giftCard.attach('#gift-card-container');
      }
    } catch (_error) {
      showError('Square payment fields could not start. Refresh the page and try again.');
      return;
    }

    giftButton?.addEventListener('click', async () => {
      clearError();
      giftButton.disabled = true;
      giftButton.setAttribute('aria-busy', 'true');
      status.textContent = 'Checking gift card…';

      let sourceId;
      try {
        sourceId = await tokenize(giftCard);
      } catch (error) {
        showError(error.message);
        giftButton.disabled = false;
        giftButton.removeAttribute('aria-busy');
        return;
      }

      secureExit();
      status.textContent = 'Submitting gift card to Square. Do not leave or submit another order…';
      startStatusWatch(payments, 'gift_card');
      try {
        const payload = await submitToken(sourceId, 'gift_card');
        if (payload.status === 'COMPLETED') {
          stopStatusWatch();
          status.textContent = 'Payment complete. Opening confirmation…';
          window.location.assign(payload.redirect_url);
          return;
        }
        stopStatusWatch();
        updateBalance(payload);
        data.giftCardApplied = true;
        giftStep.hidden = true;
        cardStep.hidden = false;
        status.textContent = `${money(payload.applied_cents)} applied. Enter a card for the remaining ${money(payload.remaining_cents)}.`;
        await initializeCard(payments);
      } catch (error) {
        const recovered = await checkExistingPayment();
        if (await applyRecoveredState(recovered, payments, 'gift_card')) {
          stopStatusWatch();
          return;
        }
        if (!error.ambiguous && recovered?.status === 'PENDING') {
          stopStatusWatch();
          showError(error.message);
          restoreCheckoutExit();
          giftButton.disabled = false;
          giftButton.removeAttribute('aria-busy');
          return;
        }
        uncertainPayment();
      }
    });

    cardButton?.addEventListener('click', async () => {
      clearError();
      cardButton.disabled = true;
      cardButton.setAttribute('aria-busy', 'true');
      status.textContent = 'Verifying card details…';

      let sourceId;
      try {
        const billingContact = Object.fromEntries(
          Object.entries(data.billingContact || {}).filter(([, value]) => value),
        );
        sourceId = await tokenize(card, {
          amount: (data.remainingCents / 100).toFixed(2),
          billingContact,
          currencyCode: 'USD',
          customerInitiated: true,
          intent: 'CHARGE',
          sellerKeyedIn: false,
        });
      } catch (error) {
        showError(error.message);
        cardButton.disabled = false;
        cardButton.removeAttribute('aria-busy');
        return;
      }

      secureExit();
      status.textContent = 'Square is processing your payment. Do not leave or submit another order…';
      startStatusWatch(payments, 'card');
      try {
        const payload = await submitToken(sourceId, 'card');
        stopStatusWatch();
        status.textContent = 'Payment complete. Opening confirmation…';
        window.location.assign(payload.redirect_url);
      } catch (error) {
        const recovered = await checkExistingPayment();
        if (await applyRecoveredState(recovered, payments, 'card')) {
          stopStatusWatch();
          return;
        }
        if (!error.ambiguous && recovered?.status === 'PARTIAL') {
          stopStatusWatch();
          showError(error.message);
          cardButton.disabled = false;
          cardButton.removeAttribute('aria-busy');
          return;
        }
        uncertainPayment();
      }
    });
  };

  initialize();
})();
