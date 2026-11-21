/**
 * NaSuite Launcher — UI Utilities, Themes, Toasts, Modals & Splash Controller
 * Exposes window.NaSuiteUI. Zero emojis; uses crisp pixel-art icons only.
 */
(function (global) {
  'use strict';

  const THEME_STORAGE_KEY = 'nasuite_launcher_theme';

  const THEMES = [
    {
      id: 'fox',
      name: 'Zorro Clasico',
      mascot: 'fox',
      accent: '#FF6B35',
      bg: '#0E1015',
      surface: '#181a24',
      mode: 'dark',
    },
    {
      id: 'panther',
      name: 'Pantera OLED',
      mascot: 'panther',
      accent: '#A855F7',
      bg: '#000000',
      surface: '#111116',
      mode: 'dark',
    },
    {
      id: 'cosmos',
      name: 'Cosmos',
      mascot: 'cosmos',
      accent: '#38BDF8',
      bg: '#0B1020',
      surface: '#131B33',
      mode: 'dark',
    },
    {
      id: 'owl',
      name: 'Buho Nocturno',
      mascot: 'owl',
      accent: '#10B981',
      bg: '#0D1317',
      surface: '#162026',
      mode: 'dark',
    },
    {
      id: 'coffee',
      name: 'Cafe Espresso',
      mascot: 'coffee',
      accent: '#D97706',
      bg: '#16110E',
      surface: '#231B16',
      mode: 'dark',
    },
    {
      id: 'penguin',
      name: 'Pinguino Artico',
      mascot: 'penguin',
      accent: '#0EA5E9',
      bg: '#0F172A',
      surface: '#1E293B',
      mode: 'dark',
    },
    {
      id: 'rabbit',
      name: 'Conejo Claro',
      mascot: 'rabbit',
      accent: '#FF6B35',
      bg: '#F8FAFC',
      surface: '#FFFFFF',
      mode: 'light',
    },
    {
      id: 'sakura',
      name: 'Sakura Claro',
      mascot: 'sakura',
      accent: '#EC4899',
      bg: '#FFF5F8',
      surface: '#FFFFFF',
      mode: 'light',
    },
  ];

  const TOAST_ICON_MAP = {
    success: 'ai',
    info: 'refresh',
    error: 'trash',
    warning: 'settings',
  };

  function icon(name, size = 16) {
    const safeName = String(name || 'file_generic').replace(/[^a-zA-Z0-9_\-/]/g, '');
    const px = Number(size) || 16;
    return `<img class="px-icon" src="/static/img/icons/${safeName}.png" width="${px}" height="${px}" alt="">`;
  }

  function mascot(name, size = 32) {
    const safeName = String(name || 'fox').replace(/[^a-zA-Z0-9_\-]/g, '');
    const px = Number(size) || 32;
    return `<img class="px-icon" src="/static/img/icons/mascots/${safeName}.png" width="${px}" height="${px}" alt="">`;
  }

  function escapeHtml(str) {
    if (str === null || str === undefined) return '';
    return String(str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  function formatBytes(bytes) {
    const num = Number(bytes);
    if (!Number.isFinite(num) || num <= 0) return '0 B';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    const idx = Math.min(Math.floor(Math.log(num) / Math.log(1024)), units.length - 1);
    const val = num / Math.pow(1024, idx);
    return `${val >= 10 || idx === 0 ? Math.round(val) : val.toFixed(1)} ${units[idx]}`;
  }

  function formatRelativeTime(iso) {
    if (!iso) return 'Nunca';
    const date = new Date(iso);
    if (Number.isNaN(date.getTime())) return String(iso);

    const diffSec = Math.floor((Date.now() - date.getTime()) / 1000);
    if (diffSec < 0) return 'Ahora mismo';
    if (diffSec < 45) return 'Ahora mismo';

    const mins = Math.floor(diffSec / 60);
    if (mins < 60) return `Hace ${mins} min`;

    const hours = Math.floor(mins / 60);
    if (hours < 24) return `Hace ${hours} h`;

    const days = Math.floor(hours / 24);
    if (days < 30) return days === 1 ? 'Hace 1 dia' : `Hace ${days} dias`;

    const months = Math.floor(days / 30);
    if (months < 12) return months === 1 ? 'Hace 1 mes' : `Hace ${months} meses`;

    const years = Math.floor(months / 12);
    return years === 1 ? 'Hace 1 anio' : `Hace ${years} anios`;
  }

  function toast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    if (!container) return;

    const safeType = TOAST_ICON_MAP[type] ? type : 'info';
    const iconName = TOAST_ICON_MAP[safeType];

    const el = document.createElement('div');
    el.className = `toast toast-${safeType}`;
    el.setAttribute('role', 'status');
    el.innerHTML = `
      <span class="toast-icon">${icon(iconName, 18)}</span>
      <span class="toast-message">${escapeHtml(message)}</span>
    `;

    let dismissed = false;
    const dismiss = () => {
      if (dismissed) return;
      dismissed = true;
      el.classList.add('toast-leaving');
      setTimeout(() => {
        if (el.parentNode) el.parentNode.removeChild(el);
      }, 260);
    };

    el.addEventListener('click', dismiss);
    container.appendChild(el);

    // Trigger entry transition if styled
    requestAnimationFrame(() => {
      el.classList.add('toast-visible');
    });

    setTimeout(dismiss, 3500);
  }

  function openModal(el) {
    if (!el) return;
    el.classList.remove('hidden');
    el.classList.add('open', 'active');
    el.setAttribute('aria-hidden', 'false');
  }

  function closeModal(el) {
    if (!el) return;
    el.classList.add('hidden');
    el.classList.remove('open', 'active');
    el.setAttribute('aria-hidden', 'true');
  }

  function confirm(title, message) {
    return new Promise((resolve) => {
      const modal = document.getElementById('modal-confirm');
      const titleEl = document.getElementById('confirm-title');
      const msgEl = document.getElementById('confirm-message');
      const btnOk = document.getElementById('btn-confirm-ok');
      const btnCancel = document.getElementById('btn-confirm-cancel');

      if (!modal || !btnOk || !btnCancel) {
        resolve(global.confirm(`${title}\n\n${message}`));
        return;
      }

      if (titleEl) titleEl.textContent = title || 'Confirmar accion';
      if (msgEl) msgEl.textContent = message || '';

      let settled = false;
      const cleanup = (result) => {
        if (settled) return;
        settled = true;
        btnOk.removeEventListener('click', onOk);
        btnCancel.removeEventListener('click', onCancel);
        modal.removeEventListener('click', onBackdrop);
        document.removeEventListener('keydown', onKeydown);
        closeModal(modal);
        resolve(result);
      };

      const onOk = () => cleanup(true);
      const onCancel = () => cleanup(false);
      const onBackdrop = (ev) => {
        if (ev.target === modal) cleanup(false);
      };
      const onKeydown = (ev) => {
        if (ev.key === 'Escape') cleanup(false);
      };

      btnOk.addEventListener('click', onOk);
      btnCancel.addEventListener('click', onCancel);
      modal.addEventListener('click', onBackdrop);
      document.addEventListener('keydown', onKeydown);

      openModal(modal);
      btnOk.focus();
    });
  }

  function setTheme(themeId) {
    const match = THEMES.find((t) => t.id === themeId);
    const validId = match ? match.id : 'fox';
    document.documentElement.setAttribute('data-theme', validId);
    try {
      localStorage.setItem(THEME_STORAGE_KEY, validId);
    } catch (_) {
      // Ignore storage errors in restricted contexts
    }
    return validId;
  }

  function getTheme() {
    const attr = document.documentElement.getAttribute('data-theme');
    if (attr && THEMES.some((t) => t.id === attr)) return attr;
    try {
      const saved = localStorage.getItem(THEME_STORAGE_KEY);
      if (saved && THEMES.some((t) => t.id === saved)) return saved;
    } catch (_) {
      // Ignore
    }
    return 'fox';
  }

  function splashProgress(pct, text) {
    const bar = document.getElementById('splash-bar');
    const status = document.getElementById('splash-status');
    const clamped = Math.max(0, Math.min(100, Number(pct) || 0));
    if (bar) {
      bar.style.width = `${clamped}%`;
    }
    if (status && typeof text === 'string') {
      status.textContent = text;
    }
  }

  function hideSplash() {
    const splash = document.getElementById('splash-screen');
    if (!splash) return;
    splash.classList.add('splash-hidden', 'fade-out');
    setTimeout(() => {
      splash.classList.add('hidden');
      splash.style.display = 'none';
    }, 380);
  }

  // Apply persisted theme immediately on script load to avoid flash
  setTheme(getTheme());

  const NaSuiteUI = {
    THEMES,
    icon,
    mascot,
    escapeHtml,
    formatBytes,
    formatRelativeTime,
    toast,
    confirm,
    openModal,
    closeModal,
    setTheme,
    getTheme,
    splashProgress,
    hideSplash,
  };

  global.NaSuiteUI = NaSuiteUI;
})(window);
