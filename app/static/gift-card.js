(() => {
  const data = window.GIFT_CARD_DATA;
  const csrf = document.querySelector('meta[name="csrf-token"]')?.content;
  const giftStep = document.querySelector('#gift-card-step');
  const cardStep = document.querySelector('#remainder-card-step');
  const giftButton = document.querySelector('#gift-card-button');
  const cardButton = document.querySelector('#remainder-card-button');
  const status = document.querySelector('#payment-status');
  const errorBox = document.querySelector('#payment-error');
  let giftCard;
  let card;

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
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || 'Square could not process that payment.');
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
  };

  const initializeCard = async (payments) => {
    if (card) return card;
    card = await payments.card();
    await card.attach('#card-container');
    return card;
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
        await initializeCard(payments);
      } else {
        giftCard = await payments.giftCard();
        await giftCard.attach('#gift-card-container');
      }
    } catch (error) {
      showError('Square payment fields could not start. Refresh the page and try again.');
      return;
    }

    giftButton?.addEventListener('click', async () => {
      clearError();
      giftButton.disabled = true;
      giftButton.setAttribute('aria-busy', 'true');
      status.textContent = 'Checking gift card…';
      try {
        const sourceId = await tokenize(giftCard);
        const payload = await submitToken(sourceId, 'gift_card');
        if (payload.status === 'COMPLETED') {
          status.textContent = 'Payment complete. Opening confirmation…';
          window.location.assign(payload.redirect_url);
          return;
        }
        updateBalance(payload);
        data.giftCardApplied = true;
        giftStep.hidden = true;
        cardStep.hidden = false;
        status.textContent = `${money(payload.applied_cents)} applied. Enter a card for the remaining ${money(payload.remaining_cents)}.`;
        await initializeCard(payments);
      } catch (error) {
        showError(error.message);
        giftButton.disabled = false;
        giftButton.removeAttribute('aria-busy');
      }
    });

    cardButton?.addEventListener('click', async () => {
      clearError();
      cardButton.disabled = true;
      cardButton.setAttribute('aria-busy', 'true');
      status.textContent = 'Processing remaining payment…';
      try {
        const billingContact = Object.fromEntries(
          Object.entries(data.billingContact || {}).filter(([, value]) => value),
        );
        const sourceId = await tokenize(card, {
          amount: (data.remainingCents / 100).toFixed(2),
          billingContact,
          currencyCode: 'USD',
          customerInitiated: true,
          intent: 'CHARGE',
          sellerKeyedIn: false,
        });
        const payload = await submitToken(sourceId, 'card');
        status.textContent = 'Payment complete. Opening confirmation…';
        window.location.assign(payload.redirect_url);
      } catch (error) {
        showError(error.message);
        cardButton.disabled = false;
        cardButton.removeAttribute('aria-busy');
      }
    });
  };

  initialize();
})();
