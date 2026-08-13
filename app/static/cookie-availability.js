(() => {
  const data = window.PIZZERIA_DATA;
  if (!data) return;

  const cookieCards = [...document.querySelectorAll('.menu-card')].filter((card) => {
    const item = data.menu?.[card.dataset.itemId];
    return item && item.name.toLocaleLowerCase().includes('cookie');
  });

  if (!cookieCards.length) return;

  const isCookieDay = (date) => {
    if (!/^\d{4}-\d{2}-\d{2}$/.test(date || '')) return false;
    const weekday = new Date(`${date}T12:00:00`).getDay();
    return weekday === 5 || weekday === 6 || weekday === 0;
  };

  const updateCookieVisibility = () => {
    const showCookies = isCookieDay(data.selectedDate);
    cookieCards.forEach((card) => {
      card.hidden = !showCookies;
    });
    cookieCards.forEach((card) => {
      const group = card.closest('.menu-group');
      if (!group) return;
      const visibleCards = [...group.querySelectorAll('.menu-card')]
        .some((candidate) => !candidate.hidden);
      group.hidden = !visibleCards;
    });
  };

  updateCookieVisibility();
  const selectedPickup = document.querySelector('#selected-pickup');
  if (selectedPickup) {
    new MutationObserver(updateCookieVisibility).observe(selectedPickup, {
      childList: true,
      characterData: true,
      subtree: true,
    });
  }
})();
