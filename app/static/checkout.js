(() => {
  const data = window.CHECKOUT_DATA;
  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  const pickupDialog = document.querySelector('#pickup-dialog');
  const slotGrid = document.querySelector('#slot-grid');
  const customTipWrap = document.querySelector('#custom-tip-wrap');
  const customTipInput = document.querySelector('#custom-tip');
  const checkoutForm = document.querySelector('#checkout-form');
  const checkoutButton = document.querySelector('.checkout-submit');
  const paymentStatus = document.querySelector('#payment-status');
  let squareCard = null;

  const escapeHtml = (value) => String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');

  const money = (cents) => `$${(cents / 100).toFixed(2)}`;

  const api = async (url, options = {}) => {
    const response = await fetch(url, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        'X-CSRF-Token': csrf,
        ...(options.headers || {}),
      },
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || 'Something went wrong.');
    return payload;
  };

  const updateTip = () => {
    const choice = document.querySelector('input[name="tip_choice"]:checked')?.value || '15';
    customTipWrap.hidden = choice !== 'custom';
    let tipCents = 0;
    let label = choice === '0' ? 'No tip' : `${choice}%`;
    if (choice === 'custom') {
      tipCents = Math.max(0, Math.round((Number.parseFloat(customTipInput.value) || 0) * 100));
      label = 'Custom';
    } else {
      tipCents = Math.round(data.tipBasisCents * Number(choice) / 100);
    }
    const totalCents = data.orderTotalCents + tipCents;
    document.querySelector('#tip-label').textContent = `(${label})`;
    document.querySelector('#tip-total').textContent = money(tipCents);
    document.querySelector('#grand-total').textContent = money(totalCents);
    document.querySelector('#submit-total').textContent = money(totalCents);
    document.querySelector('#verification-total-cents').value = String(totalCents);
    return totalCents;
  };

  const refreshQuote = async () => {
    const choice = document.querySelector('input[name="tip_choice"]:checked')?.value || '15';
    const customTipCents = Math.max(
      0,
      Math.round((Number.parseFloat(customTipInput?.value) || 0) * 100),
    );
    const quote = await api('/api/checkout-quote', {
      method: 'POST',
      body: JSON.stringify({ tip_choice: choice, custom_tip_cents: customTipCents }),
    });
    data.subtotalCents = quote.subtotal_cents;
    data.tipBasisCents = quote.tip_basis_cents;
    data.taxCents = quote.tax_cents;
    data.orderTotalCents = quote.order_total_cents;
    document.querySelector('#summary-subtotal').textContent = quote.subtotal;
    document.querySelector('#summary-tax').textContent = quote.tax;
    const discountRow = document.querySelector('#summary-discount-row');
    discountRow.hidden = quote.discount_cents === 0;
    document.querySelector('#summary-discount').textContent = `−${quote.discount}`;
    updateTip();
  };

  const renderSummary = (cart) => {
    data.cart = cart;
    data.subtotalCents = cart.totals.subtotal_cents;
    if (!data.squareDataEnabled) {
      data.tipBasisCents = data.subtotalCents;
      data.taxCents = Math.round(data.subtotalCents * data.taxRate);
      data.orderTotalCents = data.subtotalCents + data.taxCents;
    }
    const itemLabel = `${cart.totals.item_count} item${cart.totals.item_count === 1 ? '' : 's'}`;
    document.querySelector('#summary-item-count').textContent = itemLabel;
    document.querySelector('#summary-subtotal').textContent = cart.totals.subtotal;
    document.querySelector('#summary-tax').textContent = money(data.taxCents);
    document.querySelector('#summary-error').hidden = true;
    document.querySelector('#summary-lines').innerHTML = cart.lines.map((line) => {
      const increaseHitsPizzaLimit = line.capacity_category === 'pizza'
        && cart.totals.pizza_count >= data.pizzaLimit;
      const increaseHitsTotalLimit = cart.totals.item_count >= data.totalLimit;
      const increaseDisabled = increaseHitsPizzaLimit || increaseHitsTotalLimit;
      const increaseTitle = increaseHitsPizzaLimit
        ? `${data.pizzaLimit} pizza maximum reached`
        : (increaseHitsTotalLimit ? `${data.totalLimit} item maximum reached` : '');
      return `
        <div class="summary-line" data-cart-line="${escapeHtml(line.id)}">
          <div>
            <strong>${escapeHtml(line.name)}</strong>
            ${line.modifiers.length ? `<span>${line.modifiers.map(escapeHtml).join(' · ')}</span>` : ''}
            <div class="line-actions line-actions-light" aria-label="Quantity for ${escapeHtml(line.name)}">
              <div class="line-quantity">
                <button type="button" data-cart-action="decrease" data-line-id="${escapeHtml(line.id)}" data-quantity="${line.quantity}" aria-label="Decrease ${escapeHtml(line.name)} quantity" ${line.quantity === 1 ? 'disabled' : ''}>−</button>
                <strong>${line.quantity}</strong>
                <button type="button" data-cart-action="increase" data-line-id="${escapeHtml(line.id)}" data-quantity="${line.quantity}" aria-label="Increase ${escapeHtml(line.name)} quantity" ${increaseDisabled ? 'disabled' : ''} title="${escapeHtml(increaseTitle)}">+</button>
              </div>
              <button class="remove-line" type="button" data-remove-line="${escapeHtml(line.id)}">Remove</button>
            </div>
          </div>
          <span>${line.price}</span>
        </div>
      `;
    }).join('');
    updateTip();
  };

  document.querySelectorAll('input[name="tip_choice"]').forEach((input) => {
    input.addEventListener('change', updateTip);
  });
  customTipInput?.addEventListener('input', updateTip);

  document.querySelector('#summary-lines')?.addEventListener('click', async (event) => {
    const removeButton = event.target.closest('[data-remove-line]');
    const quantityButton = event.target.closest('[data-cart-action]');
    if (!removeButton && !quantityButton) return;
    const summaryError = document.querySelector('#summary-error');
    checkoutButton.disabled = true;
    try {
      let cart;
      if (removeButton) {
        cart = await api(`/api/cart/${removeButton.dataset.removeLine}`, { method: 'DELETE' });
      } else {
        const currentQuantity = Number.parseInt(quantityButton.dataset.quantity, 10);
        const change = quantityButton.dataset.cartAction === 'increase' ? 1 : -1;
        cart = await api(`/api/cart/${quantityButton.dataset.lineId}`, {
          method: 'PATCH',
          body: JSON.stringify({ quantity: currentQuantity + change }),
        });
      }
      if (cart.lines.length === 0) {
        window.location.assign('/');
        return;
      }
      renderSummary(cart);
      await refreshQuote();
    } catch (error) {
      summaryError.textContent = error.message;
      summaryError.hidden = false;
    } finally {
      checkoutButton.disabled = false;
    }
  });

  document.querySelector('#apply-code')?.addEventListener('click', () => {
    const code = document.querySelector('#discount-code').value.trim();
    const message = document.querySelector('#code-message');
    if (!code) {
      message.textContent = 'Enter a coupon or gift card code first.';
      message.classList.add('field-note-error');
      return;
    }
    message.textContent = 'Coupon and gift card redemption will be connected in the next Square step.';
    message.classList.add('field-note-error');
  });

  const loadSlots = async (date) => {
    slotGrid.innerHTML = '<p>Loading pickup times…</p>';
    document.querySelectorAll('.date-choice').forEach((button) => {
      button.classList.toggle('active', button.dataset.date === date);
    });
    try {
      const payload = await api(`/api/slots?date=${encodeURIComponent(date)}`);
      slotGrid.innerHTML = payload.slots.map((slot) => `
        <button class="slot-choice${slot.available ? '' : ' slot-choice-unavailable'}" type="button" data-service-at="${slot.iso}" ${slot.available ? '' : 'disabled'}>
          <strong>${slot.time}</strong>
          ${slot.status ? `<span>${escapeHtml(slot.status)}</span>` : ''}
        </button>
      `).join('') || '<p>No pickup times are available for this day.</p>';
    } catch (error) {
      slotGrid.innerHTML = `<p class="form-error">${escapeHtml(error.message)}</p>`;
    }
  };

  document.querySelector('#checkout-change-pickup')?.addEventListener('click', () => {
    pickupDialog.showModal();
    loadSlots(data.selectedDate);
  });

  document.querySelector('#date-row')?.addEventListener('click', (event) => {
    const button = event.target.closest('.date-choice');
    if (button) loadSlots(button.dataset.date);
  });

  slotGrid?.addEventListener('click', async (event) => {
    const button = event.target.closest('.slot-choice');
    if (!button) return;
    try {
      const selected = await api('/api/selected-slot', {
        method: 'POST',
        body: JSON.stringify({ service_at: button.dataset.serviceAt }),
      });
      document.querySelector('#checkout-date').textContent = selected.date;
      document.querySelector('#checkout-time').textContent = selected.time;
      data.selectedDate = selected.service_at.slice(0, 10);
      pickupDialog.close();
    } catch (error) {
      button.insertAdjacentHTML('afterend', `<p class="form-error">${escapeHtml(error.message)}</p>`);
    }
  });

  document.querySelectorAll('[data-close-dialog]').forEach((button) => {
    button.addEventListener('click', () => document.querySelector(`#${button.dataset.closeDialog}`).close());
  });
  pickupDialog?.addEventListener('click', (event) => {
    if (event.target === pickupDialog) pickupDialog.close();
  });

  const initializeSquareCard = async () => {
    if (data.demoMode) return;
    if (!window.Square) throw new Error('Square’s secure card form did not load. Refresh and try again.');
    const payments = window.Square.payments(
      data.squareApplicationId,
      data.squareLocationId,
    );
    squareCard = await payments.card();
    await squareCard.attach('#card-container');
  };

  checkoutForm?.addEventListener('submit', async (event) => {
    if (data.demoMode || document.querySelector('#source-id').value) return;
    event.preventDefault();
    paymentStatus.textContent = '';
    checkoutButton.disabled = true;
    checkoutButton.setAttribute('aria-busy', 'true');
    try {
      if (!squareCard) throw new Error('The secure card form is still loading. Try again in a moment.');
      const totalCents = updateTip();
      const nameParts = new FormData(checkoutForm).get('name').trim().split(/\s+/);
      const tokenResult = await squareCard.tokenize({
        amount: (totalCents / 100).toFixed(2),
        billingContact: {
          givenName: nameParts[0] || '',
          familyName: nameParts.slice(1).join(' '),
          email: new FormData(checkoutForm).get('email'),
          phone: new FormData(checkoutForm).get('phone'),
          countryCode: 'US',
        },
        currencyCode: data.currencyCode,
        intent: 'CHARGE',
        customerInitiated: true,
        sellerKeyedIn: false,
      });
      if (tokenResult.status !== 'OK') {
        throw new Error(tokenResult.errors?.[0]?.message || 'Check your card details and try again.');
      }
      document.querySelector('#source-id').value = tokenResult.token;
      HTMLFormElement.prototype.submit.call(checkoutForm);
    } catch (error) {
      paymentStatus.textContent = error.message;
      checkoutButton.disabled = false;
      checkoutButton.removeAttribute('aria-busy');
    }
  });

  renderSummary(data.cart);
  initializeSquareCard().catch((error) => {
    if (paymentStatus) paymentStatus.textContent = error.message;
  });
})();
