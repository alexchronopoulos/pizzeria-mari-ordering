(() => {
  const data = window.PIZZERIA_DATA;
  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  const pickupDialog = document.querySelector('#pickup-dialog');
  const itemDialog = document.querySelector('#item-dialog');
  const slotGrid = document.querySelector('#slot-grid');
  const itemError = document.querySelector('#item-error');
  let activeItem = null;
  let quantity = 1;
  let selectedAdditions = new Map();
  let limitAttempted = null;

  const escapeHtml = (value) => String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;');

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

  const renderCart = (cart) => {
    data.cart = cart;
    const lines = document.querySelector('#cart-lines');
    const empty = document.querySelector('#cart-empty');
    const footer = document.querySelector('#cart-footer');
    const cartError = document.querySelector('#cart-error');
    document.querySelector('#cart-count').textContent = cart.totals.item_count;
    document.querySelector('#cart-subtotal').textContent = cart.totals.subtotal;
    cartError.hidden = true;
    empty.hidden = cart.lines.length > 0;
    footer.hidden = cart.lines.length === 0;
    lines.innerHTML = cart.lines.map((line) => {
      const increaseHitsPizzaLimit = line.capacity_category === 'pizza'
        && cart.totals.pizza_count >= data.pizzaLimit;
      const increaseHitsTotalLimit = cart.totals.item_count >= data.totalLimit;
      const increaseDisabled = increaseHitsPizzaLimit || increaseHitsTotalLimit;
      const increaseTitle = increaseHitsPizzaLimit
        ? `${data.pizzaLimit} pizza maximum reached`
        : (increaseHitsTotalLimit ? `${data.totalLimit} item maximum reached` : '');
      return `
      <div class="cart-line" data-cart-line="${escapeHtml(line.id)}">
        <strong>${escapeHtml(line.name)}</strong>
        <span class="line-price">${line.price}</span>
        ${line.modifiers.length ? `<small>${line.modifiers.map(escapeHtml).join('<br>')}</small>` : ''}
        <div class="line-actions">
          <div class="line-quantity" aria-label="Quantity for ${escapeHtml(line.name)}">
            <button type="button" data-cart-action="decrease" data-line-id="${escapeHtml(line.id)}" data-quantity="${line.quantity}" aria-label="Decrease ${escapeHtml(line.name)} quantity" ${line.quantity === 1 ? 'disabled' : ''}>−</button>
            <strong>${line.quantity}</strong>
            <button type="button" data-cart-action="increase" data-line-id="${escapeHtml(line.id)}" data-quantity="${line.quantity}" aria-label="Increase ${escapeHtml(line.name)} quantity" ${increaseDisabled ? 'disabled' : ''} title="${escapeHtml(increaseTitle)}">+</button>
          </div>
          <button class="remove-line" type="button" data-remove-line="${escapeHtml(line.id)}">Remove</button>
        </div>
      </div>
    `;
    }).join('');
  };

  const loadSlots = async (date) => {
    slotGrid.innerHTML = '<p>Loading pickup times…</p>';
    document.querySelectorAll('.date-choice').forEach((button) => {
      button.classList.toggle('active', button.dataset.date === date);
    });
    try {
      const payload = await api(`/api/slots?date=${encodeURIComponent(date)}`);
      slotGrid.innerHTML = payload.slots.map((slot) => `
        <button class="slot-choice" type="button" data-service-at="${slot.iso}">
          <strong>${slot.time}</strong>
        </button>
      `).join('') || '<p>No pickup times are available for this day.</p>';
    } catch (error) {
      slotGrid.innerHTML = `<p class="form-error">${escapeHtml(error.message)}</p>`;
    }
  };

  document.querySelector('#change-pickup')?.addEventListener('click', () => {
    pickupDialog.showModal();
    const date = data.selectedDate || document.querySelector('.date-choice')?.dataset.date;
    if (date) loadSlots(date);
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
      document.querySelector('#selected-pickup').textContent = `${selected.date} at ${selected.time}`;
      data.selectedDate = selected.service_at.slice(0, 10);
      pickupDialog.close();
    } catch (error) {
      button.insertAdjacentHTML('afterend', `<p class="form-error">${escapeHtml(error.message)}</p>`);
    }
  });

  const updateAddButton = () => {
    const button = document.querySelector('#add-to-cart');
    const label = document.querySelector('#add-to-cart-label');
    const price = document.querySelector('#item-price');
    const pizzaCount = data.cart.totals.pizza_count;
    const itemCount = data.cart.totals.item_count;

    button.disabled = false;
    button.classList.remove('button-limit');
    price.hidden = false;
    label.textContent = 'Add to order';

    if (activeItem.capacity_category === 'pizza' && pizzaCount >= data.pizzaLimit) {
      label.textContent = `${data.pizzaLimit} pizza maximum reached`;
      price.hidden = true;
      button.disabled = true;
      button.classList.add('button-limit');
    } else if (itemCount >= data.totalLimit) {
      label.textContent = `${data.totalLimit} item maximum reached`;
      price.hidden = true;
      button.disabled = true;
      button.classList.add('button-limit');
    } else if (limitAttempted) {
      label.textContent = limitAttempted;
      price.hidden = true;
      button.classList.add('button-limit');
    }
  };

  const updateItemPrice = () => {
    const additionsTotal = [...selectedAdditions.entries()].reduce((total, [id, placement]) => {
      const option = activeItem.additions.find((addition) => addition.id === id);
      if (!option) return total;
      return total + (placement === 'whole' ? option.whole_price_cents : option.half_price_cents);
    }, 0);
    const total = (activeItem.price_cents + additionsTotal) * quantity;
    document.querySelector('#item-price').textContent = ` · $${(total / 100).toFixed(2)}`;
    document.querySelector('#quantity-value').textContent = quantity;
    updateAddButton();
  };

  document.querySelectorAll('.menu-card:not(:disabled)').forEach((card) => {
    card.addEventListener('click', () => {
      activeItem = data.menu[card.dataset.itemId];
      quantity = 1;
      selectedAdditions = new Map();
      limitAttempted = null;
      itemError.hidden = true;
      document.querySelector('#item-category').textContent = activeItem.category_label;
      document.querySelector('#item-name').textContent = activeItem.name;
      document.querySelector('#item-description').textContent = activeItem.description;
      document.querySelector('#item-art').className = `pizza-art pizza-${activeItem.art} pizza-large`;
      document.querySelector('#item-preferences').innerHTML = activeItem.preferences.map((preference) => `
        <label><input type="checkbox" value="${escapeHtml(preference.id)}"> <span>${escapeHtml(preference.name)}</span></label>
      `).join('');
      document.querySelector('#item-additions').innerHTML = activeItem.additions.map((addition) => `
        <div class="addition-row" data-addition-id="${escapeHtml(addition.id)}">
          <strong>${escapeHtml(addition.name)}</strong>
          <div class="placement-options" role="group" aria-label="Placement for ${escapeHtml(addition.name)}">
            <button type="button" data-placement="none" aria-pressed="true">None</button>
            <button type="button" data-placement="whole" aria-pressed="false">Whole <small>+${addition.whole_price}</small></button>
            <button type="button" data-placement="first_half" aria-pressed="false">1st half <small>+${addition.half_price}</small></button>
            <button type="button" data-placement="second_half" aria-pressed="false">2nd half <small>+${addition.half_price}</small></button>
          </div>
        </div>
      `).join('');
      updateItemPrice();
      itemDialog.showModal();
    });
  });

  document.querySelector('#quantity-down')?.addEventListener('click', () => {
    quantity = Math.max(1, quantity - 1);
    limitAttempted = null;
    updateItemPrice();
  });
  document.querySelector('#quantity-up')?.addEventListener('click', () => {
    const nextQuantity = quantity + 1;
    if (
      activeItem.capacity_category === 'pizza'
      && data.cart.totals.pizza_count + nextQuantity > data.pizzaLimit
    ) {
      limitAttempted = `Add to order · ${data.pizzaLimit} pizza maximum`;
      updateItemPrice();
      return;
    }
    if (data.cart.totals.item_count + nextQuantity > data.totalLimit) {
      limitAttempted = `Add to order · ${data.totalLimit} item maximum`;
      updateItemPrice();
      return;
    }
    quantity = nextQuantity;
    limitAttempted = null;
    updateItemPrice();
  });

  document.querySelector('#item-additions')?.addEventListener('click', (event) => {
    const button = event.target.closest('[data-placement]');
    if (!button) return;
    const row = button.closest('[data-addition-id]');
    const placement = button.dataset.placement;
    if (placement === 'none') selectedAdditions.delete(row.dataset.additionId);
    else selectedAdditions.set(row.dataset.additionId, placement);
    row.querySelectorAll('[data-placement]').forEach((choice) => {
      choice.setAttribute('aria-pressed', String(choice === button));
    });
    updateItemPrice();
  });

  document.querySelector('#add-to-cart')?.addEventListener('click', async () => {
    const preferences = [...document.querySelectorAll('#item-preferences input:checked')]
      .map((input) => input.value);
    const additions = [...selectedAdditions.entries()].map(([id, placement]) => ({ id, placement }));
    try {
      const cart = await api('/api/cart', {
        method: 'POST',
        body: JSON.stringify({ item_id: activeItem.id, quantity, preferences, additions }),
      });
      renderCart(cart);
      itemDialog.close();
      document.querySelector('#cart').scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    } catch (error) {
      itemError.textContent = error.message;
      itemError.hidden = false;
    }
  });

  document.querySelector('#cart-lines')?.addEventListener('click', async (event) => {
    const removeButton = event.target.closest('[data-remove-line]');
    const quantityButton = event.target.closest('[data-cart-action]');
    if (!removeButton && !quantityButton) return;
    const cartError = document.querySelector('#cart-error');
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
      renderCart(cart);
    } catch (error) {
      cartError.textContent = error.message;
      cartError.hidden = false;
    }
  });

  document.querySelectorAll('[data-close-dialog]').forEach((button) => {
    button.addEventListener('click', () => document.querySelector(`#${button.dataset.closeDialog}`).close());
  });

  [pickupDialog, itemDialog].forEach((dialog) => {
    dialog?.addEventListener('click', (event) => {
      if (event.target === dialog) dialog.close();
    });
  });

  renderCart(data.cart);
})();
