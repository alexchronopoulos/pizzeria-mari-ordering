(() => {
  const link = document.querySelector('#square-checkout-link');
  if (link && link.href) window.location.replace(link.href);
})();
