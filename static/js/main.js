/**
 * NaSuite Launcher — Main Application Orchestrator
 * Coordinates state, views, app cards, live task polling, repos, settings, and console drawer.
 * Zero emojis; relies strictly on window.NaSuiteAPI and window.NaSuiteUI.
 */
(function (global) {
  'use strict';

  const API = global.NaSuiteAPI;
  const UI = global.NaSuiteUI;

  const state = {
    apps: [],
    config: {},
    activeView: 'dashboard', // 'dashboard' | 'installed' | 'updates' | 'repos' | 'settings'
    selectedCategory: 'all',
    searchQuery: '',
    sortBy: 'status',
    syncing: false,
    lastSync: null,
    suiteVersion: '1.0.0',
    activeTasks: new Map(), // appId -> { taskId, type, progress, status, message, timerId }
    busyApps: new Set(), // appIds with an immediate action in flight (launch/stop/uninstall)
    consoleOpen: false,
    consolePollId: null,
    appPollId: null,
    autoSyncId: null,
    detailAppId: null,
    notifications: [],
    unreadNotifications: 0,
    notifFilter: 'all',
    notifModalOpen: false,
  };

  /* --------------------------------------------------------------------------
   * Helpers & Data Normalization
   * ----------------------------------------------------------------------- */

  function $(id) {
    return document.getElementById(id);
  }

  function normalizeStatus(app) {
    if (!app) return 'not_installed';
    if (app.running === true || app.is_running === true || app.status === 'running') {
      return 'running';
    }
    if (app.status === 'update_available' || app.update_available === true) {
      return 'update_available';
    }
    if (app.status === 'installed' || app.installed === true || app.is_installed === true) {
      return 'installed';
    }
    return 'not_installed';
  }

  function isAppInstalled(app) {
    const st = normalizeStatus(app);
    return st === 'running' || st === 'installed' || st === 'update_available';
  }

  function canUninstallApp(app) {
    if (!isAppInstalled(app)) return false;
    if (app.can_uninstall === false || app.managed === false) return false;
    return true;
  }

  function resolveIconHtml(app, size = 48) {
    const raw =
      (app && (app.icon_url || app.icon_hd || app.icon_small_url || app.icon_32 || app.icon)) ||
      'fox';
    const isSmooth = app && app.is_pixel_icon === false;
    const cls = isSmooth ? 'app-logo-smooth app-card-icon' : 'px-icon app-card-icon';
    if (typeof raw === 'string' && (raw.startsWith('/') || raw.startsWith('http'))) {
      return `<img class="${cls}" src="${UI.escapeHtml(raw)}" width="${size}" height="${size}" alt="">`;
    }
    const cleanName = String(raw).replace(/\.png$/i, '');
    return `<img class="${cls}" src="/static/img/icons/${UI.escapeHtml(cleanName)}.png" width="${size}" height="${size}" alt="">`;
  }

  function shortSha(sha) {
    if (!sha) return '';
    return String(sha).trim().slice(0, 7);
  }

  function getAppRepoSlug(app) {
    if (app.full_name) return app.full_name;
    if (app.repo_full_name) return app.repo_full_name;
    if (app.owner && app.repo_name) return `${app.owner}/${app.repo_name}`;
    return app.repo_name || app.id || '';
  }

  function getStatusMeta(status) {
    switch (status) {
      case 'running':
        return { label: 'En ejecucion', cls: 'status-running', order: 0 };
      case 'update_available':
        return { label: 'Actualizacion disponible', cls: 'status-update', order: 1 };
      case 'installed':
        return { label: 'Instalada', cls: 'status-installed', order: 2 };
      default:
        return { label: 'Disponible en GitHub', cls: 'status-available', order: 3 };
    }
  }

  function extractAppsPayload(res) {
    if (Array.isArray(res)) {
      return { apps: res };
    }
    if (res && typeof res === 'object') {
      return {
        apps: Array.isArray(res.apps) ? res.apps : [],
        lastSync: res.last_sync || res.lastSync || null,
        suiteVersion: res.suite_version || res.version || null,
        githubUser: res.github_user || null,
      };
    }
    return { apps: [] };
  }

  function extractConfigPayload(res) {
    if (!res || typeof res !== 'object') return {};
    return res.config && typeof res.config === 'object' ? res.config : res;
  }

  function extractTaskPayload(res) {
    if (!res || typeof res !== 'object') return null;
    return res.task && typeof res.task === 'object' ? res.task : res;
  }

  /* --------------------------------------------------------------------------
   * View Navigation & Sidebar Stats
   * ----------------------------------------------------------------------- */

  function switchView(viewName) {
    state.activeView = viewName;

    // Highlight sidebar nav button
    document.querySelectorAll('[data-view]').forEach((btn) => {
      const isActive = btn.getAttribute('data-view') === viewName;
      btn.classList.toggle('active', isActive);
      btn.setAttribute('aria-current', isActive ? 'page' : 'false');
    });

    const isDash = viewName === 'dashboard' || viewName === 'installed' || viewName === 'updates';
    const viewMap = {
      'view-dashboard': isDash,
      'view-repos': viewName === 'repos',
      'view-settings': viewName === 'settings',
    };

    Object.entries(viewMap).forEach(([id, show]) => {
      const el = $(id);
      if (!el) return;
      el.classList.toggle('active', show);
      el.classList.toggle('hidden', !show);
    });

    if (isDash) {
      renderDashboard();
    } else if (viewName === 'repos') {
      renderReposView();
    } else if (viewName === 'settings') {
      renderSettingsView();
    }
  }

  function updateSidebarBadgesAndStats() {
    const allCount = state.apps.length;
    let installedCount = 0;
    let updatesCount = 0;
    let runningCount = 0;

    for (const app of state.apps) {
      const st = normalizeStatus(app);
      if (st === 'running') {
        runningCount++;
        installedCount++;
      } else if (st === 'update_available') {
        updatesCount++;
        installedCount++;
      } else if (st === 'installed') {
        installedCount++;
      }
    }

    const setBadge = (id, count) => {
      const el = $(id);
      if (!el) return;
      el.textContent = String(count);
      el.classList.toggle('badge-zero', count === 0);
    };

    setBadge('badge-all', allCount);
    setBadge('badge-installed', installedCount);
    setBadge('badge-updates', updatesCount);
    setBadge('badge-running', runningCount);

    const userEl = $('stat-github-user');
    if (userEl) {
      userEl.textContent = state.config.github_user || 'NachoFari';
    }

    const syncEl = $('stat-last-sync');
    if (syncEl) {
      syncEl.textContent = state.lastSync ? UI.formatRelativeTime(state.lastSync) : 'Pendiente';
    }

    const verEl = $('stat-suite-version');
    if (verEl) {
      verEl.textContent = state.suiteVersion || '1.0.0';
    }
  }

  function setSyncStatus(text, isSpinning = false) {
    const statusEl = $('sync-status-text');
    if (statusEl) statusEl.textContent = text;
    const btnSync = $('btn-sync');
    if (btnSync) {
      btnSync.disabled = isSpinning;
      btnSync.classList.toggle('is-syncing', isSpinning);
    }
  }

  /* --------------------------------------------------------------------------
   * Dashboard: Filtering, Sorting, Category Chips & App Cards
   * ----------------------------------------------------------------------- */

  function getFilteredApps() {
    const query = state.searchQuery.trim().toLowerCase();

    let list = state.apps.filter((app) => {
      const st = normalizeStatus(app);

      if (state.activeView === 'installed' && !isAppInstalled(app)) return false;
      if (state.activeView === 'updates' && st !== 'update_available') return false;

      if (state.selectedCategory !== 'all') {
        const cat = (app.category || 'General').trim();
        if (cat.toLowerCase() !== state.selectedCategory.toLowerCase()) return false;
      }

      if (query) {
        const topics = Array.isArray(app.topics) ? app.topics.join(' ') : '';
        const haystack = [
          app.name,
          app.repo_name,
          app.full_name,
          app.description,
          app.category,
          topics,
        ]
          .filter(Boolean)
          .join(' ')
          .toLowerCase();
        if (!haystack.includes(query)) return false;
      }

      return true;
    });

    const priorityRank = { natex: 0, naspire: 1, nasight: 2 };
    list = list.slice().sort((a, b) => {
      // Flagship NaTex is always #1 in Aplicaciones de NaSuite
      const pa = priorityRank[String(a.id || '').toLowerCase()] ?? 99;
      const pb = priorityRank[String(b.id || '').toLowerCase()] ?? 99;
      if (pa === 0 && pb !== 0) return -1;
      if (pb === 0 && pa !== 0) return 1;

      if (state.sortBy === 'name') {
        return String(a.name || '').localeCompare(String(b.name || ''), 'es', {
          sensitivity: 'base',
        });
      }
      if (state.sortBy === 'updated') {
        const ta = new Date(a.updated_at || a.pushed_at || 0).getTime() || 0;
        const tb = new Date(b.updated_at || b.pushed_at || 0).getTime() || 0;
        if (tb !== ta) return tb - ta;
        return String(a.name || '').localeCompare(String(b.name || ''), 'es', {
          sensitivity: 'base',
        });
      }
      // Default: priority flagship order for NaSuite apps, then status, then name
      if (pa !== pb) return pa - pb;
      const oa = getStatusMeta(normalizeStatus(a)).order;
      const ob = getStatusMeta(normalizeStatus(b)).order;
      if (oa !== ob) return oa - ob;
      return String(a.name || '').localeCompare(String(b.name || ''), 'es', {
        sensitivity: 'base',
      });
    });

    return list;
  }

  function renderCategoryChips() {
    const container = $('category-chips');
    if (!container) return;

    const catMap = new Map();
    for (const app of state.apps) {
      const cat = (app.category || 'General').trim() || 'General';
      catMap.set(cat, (catMap.get(cat) || 0) + 1);
    }

    const categories = Array.from(catMap.keys()).sort((a, b) =>
      a.localeCompare(b, 'es', { sensitivity: 'base' })
    );

    if (
      state.selectedCategory !== 'all' &&
      !categories.some((c) => c.toLowerCase() === state.selectedCategory.toLowerCase())
    ) {
      state.selectedCategory = 'all';
    }

    const chipsHtml = [
      `<button type="button" class="chip ${state.selectedCategory === 'all' ? 'active' : ''}" data-category="all">Todas <span class="chip-count">${state.apps.length}</span></button>`,
      ...categories.map((cat) => {
        const active = state.selectedCategory.toLowerCase() === cat.toLowerCase();
        return `<button type="button" class="chip ${active ? 'active' : ''}" data-category="${UI.escapeHtml(cat)}">${UI.escapeHtml(cat)} <span class="chip-count">${catMap.get(cat)}</span></button>`;
      }),
    ].join('');

    container.innerHTML = chipsHtml;
  }

  function buildVersionPills(app) {
    const localVer = app.local_version || (app.local_commit ? `#${shortSha(app.local_commit)}` : '');
    const remoteVer =
      app.remote_version ||
      app.latest_version ||
      app.release_tag ||
      (app.remote_commit ? `#${shortSha(app.remote_commit)}` : '');
    const st = normalizeStatus(app);

    const pills = [];
    if (localVer && isAppInstalled(app)) {
      pills.push(
        `<span class="version-pill version-local" title="Version instalada">Local: ${UI.escapeHtml(localVer)}</span>`
      );
    }
    if (remoteVer) {
      const isNew = st === 'update_available';
      pills.push(
        `<span class="version-pill ${isNew ? 'version-remote-new' : 'version-remote'}" title="Ultima version en GitHub">GitHub: ${UI.escapeHtml(remoteVer)}</span>`
      );
    }
    if (app.category) {
      pills.push(`<span class="version-pill version-category">${UI.escapeHtml(app.category)}</span>`);
    }
    return pills.join('');
  }

  function buildCardActions(app, status, activeTask) {
    const id = UI.escapeHtml(app.id);
    const isBusy = state.busyApps.has(app.id) || Boolean(activeTask);
    const disabledAttr = isBusy ? 'disabled' : '';

    let primaryHtml = '';
    if (activeTask) {
      const label = activeTask.type === 'update' ? 'Actualizando...' : 'Instalando...';
      primaryHtml = `
        <button type="button" class="btn btn-primary btn-working" disabled>
          ${UI.icon('refresh', 16)}
          <span>${label}</span>
        </button>
      `;
    } else if (status === 'running') {
      primaryHtml = `
        <button type="button" class="btn btn-danger" data-action="stop" data-app-id="${id}" ${disabledAttr}>
          ${UI.icon('terminal', 16)}
          <span>Detener</span>
        </button>
        <button type="button" class="btn btn-secondary" data-action="launch" data-app-id="${id}" title="Abrir otra instancia" ${disabledAttr}>
          ${UI.icon('compile', 16)}
          <span>Abrir otra</span>
        </button>
      `;
    } else if (status === 'update_available') {
      primaryHtml = `
        <button type="button" class="btn btn-primary btn-update" data-action="update" data-app-id="${id}" ${disabledAttr}>
          ${UI.icon('refresh', 16)}
          <span>Actualizar</span>
        </button>
        <button type="button" class="btn btn-secondary" data-action="launch" data-app-id="${id}" ${disabledAttr}>
          ${UI.icon('compile', 16)}
          <span>Iniciar</span>
        </button>
      `;
    } else if (status === 'installed') {
      primaryHtml = `
        <button type="button" class="btn btn-primary" data-action="launch" data-app-id="${id}" ${disabledAttr}>
          ${UI.icon('compile', 16)}
          <span>Iniciar</span>
        </button>
      `;
    } else {
      primaryHtml = `
        <button type="button" class="btn btn-primary btn-install" data-action="install" data-app-id="${id}" ${disabledAttr}>
          ${UI.icon('download', 16)}
          <span>Descargar e Instalar</span>
        </button>
      `;
    }

    const secondaryBtns = [];
    secondaryBtns.push(`
      <button type="button" class="btn btn-secondary btn-github" data-action="open-github" data-app-id="${id}" title="Ver repositorio en GitHub">
        ${UI.icon('upload', 15)}
        <span>GitHub</span>
      </button>
    `);
    if (isAppInstalled(app)) {
      secondaryBtns.push(`
        <button type="button" class="btn-icon" data-action="open-folder" data-app-id="${id}" title="Abrir carpeta local" ${disabledAttr}>
          ${UI.icon('folder', 16)}
        </button>
      `);
    }
    secondaryBtns.push(`
      <button type="button" class="btn-icon" data-action="details" data-app-id="${id}" title="Detalles y notas de version">
        ${UI.icon('catalog', 16)}
      </button>
    `);
    if (canUninstallApp(app)) {
      secondaryBtns.push(`
        <button type="button" class="btn-icon btn-icon-danger" data-action="uninstall" data-app-id="${id}" title="Desinstalar aplicacion" ${disabledAttr}>
          ${UI.icon('trash', 16)}
        </button>
      `);
    }

    return `
      <div class="app-card-actions">
        <div class="app-card-primary-actions">${primaryHtml}</div>
        <div class="app-card-secondary-actions">${secondaryBtns.join('')}</div>
      </div>
    `;
  }

  function buildCardProgress(activeTask) {
    if (!activeTask) return '';
    const pct = Math.max(0, Math.min(100, Number(activeTask.progress) || 0));
    const msg = activeTask.message || 'Procesando...';
    return `
      <div class="card-progress" data-task-id="${UI.escapeHtml(activeTask.taskId)}">
        <div class="card-progress-header">
          <span class="card-progress-text">${UI.escapeHtml(msg)}</span>
          <span class="card-progress-pct">${Math.round(pct)}%</span>
        </div>
        <div class="card-progress-track">
          <div class="card-progress-fill" style="width: ${pct}%"></div>
        </div>
      </div>
    `;
  }

  function renderAppCard(app) {
    const status = normalizeStatus(app);
    const meta = getStatusMeta(status);
    const activeTask = state.activeTasks.get(app.id) || null;
    const slug = getAppRepoSlug(app);
    const desc = app.description || 'Proyecto disponible en GitHub.';
    const previewUrl = app.preview_image || '';
    const isContainBanner =
      previewUrl &&
      (previewUrl === app.icon_url ||
        /badge_256|logo|favicon|icon|marcador/i.test(previewUrl));
    const bannerHtml = previewUrl
      ? `
        <div class="app-card-banner">
          <img
            class="app-card-banner-img ${isContainBanner ? 'banner-img-contain' : ''}"
            src="${UI.escapeHtml(previewUrl)}"
            alt="${UI.escapeHtml(app.name || app.id)}"
            loading="lazy"
            onerror="this.parentElement.classList.add('banner-fallback'); this.remove();"
          >
          <div class="app-card-banner-fallback">
            ${resolveIconHtml(app, 54)}
          </div>
          <span class="status-badge ${meta.cls} status-badge-floating">
            <span class="status-dot"></span>
            ${UI.escapeHtml(meta.label)}
          </span>
        </div>
      `
      : `
        <div class="app-card-banner banner-fallback">
          <div class="app-card-banner-fallback">
            ${resolveIconHtml(app, 54)}
          </div>
          <span class="status-badge ${meta.cls} status-badge-floating">
            <span class="status-dot"></span>
            ${UI.escapeHtml(meta.label)}
          </span>
        </div>
      `;

    return `
      <article class="app-card ${activeTask ? 'is-task-active' : ''}" data-app-id="${UI.escapeHtml(app.id)}">
        ${bannerHtml}
        <div class="app-card-body">
          <div class="app-card-header">
            <div class="app-card-icon-wrap">
              ${resolveIconHtml(app, 44)}
            </div>
            <div class="app-card-title-group">
              <h3 class="app-card-name" title="${UI.escapeHtml(app.name || app.id)}">${UI.escapeHtml(app.name || app.id)}</h3>
              <span class="app-card-repo" title="${UI.escapeHtml(slug)}">${UI.escapeHtml(slug)}</span>
            </div>
          </div>

          <div class="app-card-versions">
            ${buildVersionPills(app)}
          </div>

          <p class="app-card-desc">${UI.escapeHtml(desc)}</p>

          ${buildCardProgress(activeTask)}
          ${buildCardActions(app, status, activeTask)}
        </div>
      </article>
    `;
  }

  function renderDashboard() {
    renderCategoryChips();
    const grid = $('apps-grid');
    const empty = $('apps-empty');
    if (!grid) return;

    const filtered = getFilteredApps();
    if (filtered.length === 0) {
      grid.innerHTML = '';
      if (empty) {
        empty.classList.remove('hidden');
        empty.classList.add('active');
      }
      return;
    }

    if (empty) {
      empty.classList.add('hidden');
      empty.classList.remove('active');
    }

    const suiteApps = filtered.filter((a) => a.is_suite_app !== false);
    const moreApps = filtered.filter((a) => a.is_suite_app === false);

    const sections = [];
    if (suiteApps.length > 0) {
      sections.push(`
        <div class="dashboard-group">
          <div class="dashboard-group-header">
            <div class="dashboard-group-title-wrap">
              <img class="px-icon" src="/static/img/nasuite_logo.png" width="22" height="22" alt="">
              <h3 class="dashboard-group-title">Aplicaciones de <span class="text-accent">NaSuite</span></h3>
              <span class="dashboard-group-count">${suiteApps.length}</span>
            </div>
            <p class="dashboard-group-sub">Herramientas principales del ecosistema NaSuite listas para iniciar o instalar.</p>
          </div>
          <div class="apps-section-grid">
            ${suiteApps.map(renderAppCard).join('')}
          </div>
        </div>
      `);
    }

    if (moreApps.length > 0) {
      sections.push(`
        <div class="dashboard-group">
          <div class="dashboard-group-header">
            <div class="dashboard-group-title-wrap">
              ${UI.icon('catalog', 20)}
              <h3 class="dashboard-group-title">Mas de <span class="text-accent">NachoFari</span></h3>
              <span class="dashboard-group-count">${moreApps.length}</span>
            </div>
            <p class="dashboard-group-sub">Otros repositorios y proyectos publicos disponibles en GitHub.</p>
          </div>
          <div class="apps-section-grid">
            ${moreApps.map(renderAppCard).join('')}
          </div>
        </div>
      `);
    }

    grid.innerHTML = sections.join('');
  }

  /* --------------------------------------------------------------------------
   * Repos Management View
   * ----------------------------------------------------------------------- */

  function renderReposView() {
    const tbody = $('repos-table-body');
    if (!tbody) return;

    if (state.apps.length === 0) {
      tbody.innerHTML = `
        <tr>
          <td colspan="6" class="table-empty">
            No se encontraron repositorios. Presiona Sincronizar o agrega un repositorio manualmente.
          </td>
        </tr>
      `;
      return;
    }

    const customSet = new Set(
      (state.config.custom_repos || []).map((r) => String(r).toLowerCase())
    );

    tbody.innerHTML = state.apps
      .map((app) => {
        const status = normalizeStatus(app);
        const meta = getStatusMeta(status);
        const slug = getAppRepoSlug(app);
        const isCustom = Boolean(
          app.is_custom || app.custom || customSet.has(slug.toLowerCase())
        );
        const tag = app.remote_version || app.release_tag || app.latest_version || '-';
        const sha = shortSha(app.remote_commit || app.local_commit) || '-';
        const id = UI.escapeHtml(app.id);

        const actionBtns = [];
        if (status === 'not_installed') {
          actionBtns.push(`
            <button type="button" class="btn btn-sm btn-primary" data-repo-action="install" data-app-id="${id}" title="Descargar e instalar">
              ${UI.icon('download', 14)}
              <span>Instalar</span>
            </button>
          `);
        } else if (status === 'update_available') {
          actionBtns.push(`
            <button type="button" class="btn btn-sm btn-primary" data-repo-action="update" data-app-id="${id}" title="Actualizar">
              ${UI.icon('refresh', 14)}
              <span>Actualizar</span>
            </button>
          `);
        }
        actionBtns.push(`
          <button type="button" class="btn-icon" data-repo-action="open-github" data-app-id="${id}" title="Abrir en GitHub">
            ${UI.icon('catalog', 16)}
          </button>
        `);
        if (isCustom) {
          actionBtns.push(`
            <button type="button" class="btn-icon btn-icon-danger" data-repo-action="remove-repo" data-repo-slug="${UI.escapeHtml(slug)}" title="Quitar repositorio personalizado">
              ${UI.icon('trash', 16)}
            </button>
          `);
        }

        return `
          <tr>
            <td>
              <div class="repo-cell-main">
                ${resolveIconHtml(app, 24)}
                <div>
                  <div class="repo-cell-name">${UI.escapeHtml(app.name || app.id)}</div>
                  <div class="repo-cell-slug">${UI.escapeHtml(slug)}${isCustom ? ' <span class="tag-custom">Personalizado</span>' : ''}</div>
                </div>
              </div>
            </td>
            <td><span class="mono-pill">${UI.escapeHtml(tag)}</span></td>
            <td><span class="mono-pill">${UI.escapeHtml(sha)}</span></td>
            <td>
              <span class="status-badge ${meta.cls}">
                <span class="status-dot"></span>
                ${UI.escapeHtml(meta.label)}
              </span>
            </td>
            <td class="repo-cell-updated">${UI.escapeHtml(UI.formatRelativeTime(app.updated_at || app.pushed_at))}</td>
            <td class="repo-cell-actions">${actionBtns.join('')}</td>
          </tr>
        `;
      })
      .join('');
  }

  /* --------------------------------------------------------------------------
   * Settings & Theme Picker View
   * ----------------------------------------------------------------------- */

  function renderThemeGrid() {
    const grid = $('theme-grid');
    if (!grid) return;

    const currentTheme = UI.getTheme();
    grid.innerHTML = UI.THEMES.map((theme) => {
      const isSelected = theme.id === currentTheme;
      return `
        <button
          type="button"
          class="theme-card ${isSelected ? 'active' : ''}"
          data-theme-id="${UI.escapeHtml(theme.id)}"
          aria-pressed="${isSelected ? 'true' : 'false'}"
        >
          <div class="theme-card-preview" style="background: ${theme.bg}; border-color: ${theme.accent};">
            ${UI.mascot(theme.mascot, 36)}
            <span class="theme-swatch" style="background: ${theme.accent};"></span>
          </div>
          <div class="theme-card-info">
            <span class="theme-card-name">${UI.escapeHtml(theme.name)}</span>
            <span class="theme-card-mode">${theme.mode === 'light' ? 'Claro' : 'Oscuro'}</span>
          </div>
        </button>
      `;
    }).join('');
  }

  function populateSettings() {
    const cfg = state.config || {};
    const setVal = (id, val) => {
      const el = $(id);
      if (el && val !== undefined && val !== null) el.value = String(val);
    };
    const setChecked = (id, val) => {
      const el = $(id);
      if (el && el.type === 'checkbox') {
        el.checked = Boolean(val);
      }
    };

    setVal('cfg-github-user', cfg.github_user ?? 'NachoFari');
    setVal('cfg-github-token', cfg.github_token ?? '');
    setVal('cfg-workspace-dir', cfg.workspace_dir ?? '');
    setVal('cfg-install-dir', cfg.install_dir ?? '');
    setVal('cfg-prefix', cfg.repo_filter_prefix ?? cfg.repo_prefix ?? cfg.prefix ?? 'Na');
    setVal('cfg-topic', cfg.repo_filter_topic ?? cfg.repo_topic ?? cfg.topic ?? 'nasuite');

    const autoSyncEl = $('cfg-autosync');
    if (autoSyncEl) {
      if (autoSyncEl.type === 'checkbox') {
        autoSyncEl.checked = (cfg.auto_sync_minutes ?? 15) > 0;
      } else {
        autoSyncEl.value = String(cfg.auto_sync_minutes ?? 15);
      }
    }

    setChecked('cfg-start-with-windows', cfg.start_with_windows !== false);
    setChecked('cfg-minimize-on-launch', cfg.minimize_to_tray_on_launch !== false);
    setChecked('cfg-minimize-on-close', cfg.minimize_to_tray_on_close !== false);
    setChecked('cfg-auto-update', cfg.auto_update_apps !== false);

    renderThemeGrid();
  }

  function renderSettingsView() {
    populateSettings();
  }

  async function saveSettings() {
    const btn = $('btn-save-config');
    if (btn) btn.disabled = true;

    const autoSyncEl = $('cfg-autosync');
    let autoSyncMinutes = state.config.auto_sync_minutes ?? 15;
    if (autoSyncEl) {
      if (autoSyncEl.type === 'checkbox') {
        autoSyncMinutes = autoSyncEl.checked ? (Number(state.config.auto_sync_minutes) || 15) : 0;
      } else {
        autoSyncMinutes = Math.max(0, parseInt(autoSyncEl.value || '15', 10) || 0);
      }
    }

    const prefixVal = ($('cfg-prefix')?.value || '').trim();
    const topicVal = ($('cfg-topic')?.value || '').trim();
    const startWithWindows = $('cfg-start-with-windows')?.checked ?? true;
    const minimizeOnLaunch = $('cfg-minimize-on-launch')?.checked ?? true;
    const minimizeOnClose = $('cfg-minimize-on-close')?.checked ?? true;
    const autoUpdateApps = $('cfg-auto-update')?.checked ?? true;

    const payload = {
      ...state.config,
      github_user: ($('cfg-github-user')?.value || '').trim() || 'NachoFari',
      github_token: ($('cfg-github-token')?.value || '').trim(),
      workspace_dir: ($('cfg-workspace-dir')?.value || '').trim(),
      install_dir: ($('cfg-install-dir')?.value || '').trim(),
      repo_filter_prefix: prefixVal,
      repo_prefix: prefixVal,
      prefix: prefixVal,
      repo_filter_topic: topicVal,
      repo_topic: topicVal,
      topic: topicVal,
      auto_sync_minutes: autoSyncMinutes,
      start_with_windows: startWithWindows,
      minimize_to_tray_on_launch: minimizeOnLaunch,
      minimize_to_tray_on_close: minimizeOnClose,
      auto_update_apps: autoUpdateApps,
      theme: UI.getTheme(),
    };

    try {
      const saved = await API.saveConfig(payload);
      state.config = { ...payload, ...extractConfigPayload(saved) };
      API.setAutostart(startWithWindows).catch(() => {});
      updateSidebarBadgesAndStats();
      setupAutoSyncTimer();
      UI.toast('Configuracion guardada correctamente.', 'success');

      const shouldSync = await UI.confirm(
        'Sincronizar ahora',
        'Deseas sincronizar los repositorios de GitHub ahora con la nueva configuracion?'
      );
      if (shouldSync) {
        triggerSync(false);
      }
    } catch (err) {
      UI.toast(`Error al guardar configuracion: ${err.message}`, 'error');
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  function handleSaveConfig() {
    return saveSettings();
  }

  function applyThemeSelection(themeId, notify = false) {
    const applied = UI.setTheme(themeId);
    state.config.theme = applied;
    renderThemeGrid();

    const themeObj = UI.THEMES.find((t) => t.id === applied);
    if (notify && themeObj) {
      UI.toast(`Tema aplicado: ${themeObj.name}`, 'info');
    }

    // Persist theme quietly to backend config
    if (Object.keys(state.config).length > 0) {
      API.saveConfig({ ...state.config, theme: applied }).catch(() => {});
    }
  }

  function cycleQuickTheme() {
    const current = UI.getTheme();
    const idx = UI.THEMES.findIndex((t) => t.id === current);
    const next = UI.THEMES[(idx + 1) % UI.THEMES.length];
    applyThemeSelection(next.id, true);
  }

  /* --------------------------------------------------------------------------
   * App Detail Modal
   * ----------------------------------------------------------------------- */

  function openAppDetailModal(appId) {
    const app = state.apps.find((a) => a.id === appId);
    if (!app) return;

    state.detailAppId = appId;
    const modal = $('modal-app-detail');
    const titleEl = $('detail-title');
    const bodyEl = $('detail-body');
    const actionsEl = $('detail-actions');
    if (!modal || !bodyEl) return;

    const status = normalizeStatus(app);
    const meta = getStatusMeta(status);
    const slug = getAppRepoSlug(app);
    const notes = app.release_notes || app.release_body || '';
    const installPath = app.installed_path || app.local_path || 'No instalada localmente';
    const topics = Array.isArray(app.topics) ? app.topics : [];

    if (titleEl) {
      titleEl.innerHTML = `${resolveIconHtml(app, 28)} <span>${UI.escapeHtml(app.name || app.id)}</span>`;
    }

    bodyEl.innerHTML = `
      <div class="detail-header-row">
        <span class="status-badge ${meta.cls}">
          <span class="status-dot"></span>
          ${UI.escapeHtml(meta.label)}
        </span>
        <span class="detail-repo-slug">${UI.escapeHtml(slug)}</span>
      </div>

      <p class="detail-description">${UI.escapeHtml(app.description || 'Sin descripcion.')}</p>

      <div class="detail-meta-grid">
        <div class="detail-meta-item">
          <span class="detail-meta-label">Version Local</span>
          <span class="detail-meta-value">${UI.escapeHtml(app.local_version || shortSha(app.local_commit) || '-')}</span>
        </div>
        <div class="detail-meta-item">
          <span class="detail-meta-label">Version GitHub</span>
          <span class="detail-meta-value">${UI.escapeHtml(app.remote_version || app.release_tag || shortSha(app.remote_commit) || '-')}</span>
        </div>
        <div class="detail-meta-item">
          <span class="detail-meta-label">Ultima Actualizacion</span>
          <span class="detail-meta-value">${UI.escapeHtml(UI.formatRelativeTime(app.updated_at || app.pushed_at))}</span>
        </div>
        <div class="detail-meta-item">
          <span class="detail-meta-label">Categoria</span>
          <span class="detail-meta-value">${UI.escapeHtml(app.category || 'General')}</span>
        </div>
        <div class="detail-meta-item detail-meta-wide">
          <span class="detail-meta-label">Ruta Local</span>
          <span class="detail-meta-value mono-text">${UI.escapeHtml(installPath)}</span>
        </div>
      </div>

      ${
        topics.length > 0
          ? `<div class="detail-topics">${topics
              .map((t) => `<span class="topic-pill">${UI.escapeHtml(t)}</span>`)
              .join('')}</div>`
          : ''
      }

      <div class="detail-release-notes">
        <h4>Notas de Version</h4>
        <pre class="release-notes-pre">${UI.escapeHtml(
          notes || 'No hay notas de lanzamiento publicadas para esta version.'
        )}</pre>
      </div>
    `;

    if (actionsEl) {
      const id = UI.escapeHtml(app.id);
      const btns = [];

      if (status === 'running') {
        btns.push(`
          <button type="button" class="btn btn-danger" data-detail-action="stop" data-app-id="${id}">
            ${UI.icon('terminal', 16)} <span>Detener</span>
          </button>
        `);
      } else if (status === 'update_available') {
        btns.push(`
          <button type="button" class="btn btn-primary" data-detail-action="update" data-app-id="${id}">
            ${UI.icon('refresh', 16)} <span>Actualizar</span>
          </button>
          <button type="button" class="btn btn-secondary" data-detail-action="launch" data-app-id="${id}">
            ${UI.icon('compile', 16)} <span>Iniciar</span>
          </button>
        `);
      } else if (status === 'installed') {
        btns.push(`
          <button type="button" class="btn btn-primary" data-detail-action="launch" data-app-id="${id}">
            ${UI.icon('compile', 16)} <span>Iniciar</span>
          </button>
        `);
      } else {
        btns.push(`
          <button type="button" class="btn btn-primary" data-detail-action="install" data-app-id="${id}">
            ${UI.icon('download', 16)} <span>Descargar e Instalar</span>
          </button>
        `);
      }

      if (isAppInstalled(app)) {
        btns.push(`
          <button type="button" class="btn btn-secondary" data-detail-action="open-folder" data-app-id="${id}">
            ${UI.icon('folder', 16)} <span>Abrir Carpeta</span>
          </button>
        `);
      }

      btns.push(`
        <button type="button" class="btn btn-secondary" data-detail-action="open-github" data-app-id="${id}">
          ${UI.icon('catalog', 16)} <span>Ver en GitHub</span>
        </button>
      `);

      actionsEl.innerHTML = btns.join('');
    }

    UI.openModal(modal);
  }

  function closeAppDetailModal() {
    state.detailAppId = null;
    UI.closeModal($('modal-app-detail'));
  }

  /* --------------------------------------------------------------------------
   * Notifications Center ("What's New")
   * ----------------------------------------------------------------------- */

  function handleUpdate(appId) {
    return handleAppAction('update', appId);
  }

  function handleInstall(appId) {
    return handleAppAction('install', appId);
  }

  function openGithub(appId, fallbackUrl = '') {
    if (appId && state.apps.some((a) => a.id === appId)) {
      return handleAppAction('open-github', appId);
    }
    if (fallbackUrl) {
      global.open(fallbackUrl, '_blank', 'noopener,noreferrer');
    }
    return Promise.resolve();
  }

  function extractNotificationsPayload(res) {
    if (Array.isArray(res)) {
      const unread = res.filter((n) => n && !n.read).length;
      return { items: res, unreadCount: unread };
    }
    if (res && typeof res === 'object') {
      const items = Array.isArray(res.notifications)
        ? res.notifications
        : Array.isArray(res.items)
        ? res.items
        : [];
      const unreadCount =
        typeof res.unread_count === 'number'
          ? res.unread_count
          : items.filter((n) => n && !n.read).length;
      return { items, unreadCount };
    }
    return { items: [], unreadCount: 0 };
  }

  function getNotifKindMeta(kind) {
    switch (String(kind || '').toLowerCase()) {
      case 'release':
        return { label: 'Release', cls: 'notif-kind-release' };
      case 'commit':
        return { label: 'Commit', cls: 'notif-kind-commit' };
      case 'new_repo':
        return { label: 'Repo Nuevo', cls: 'notif-kind-new_repo' };
      case 'auto_update':
        return { label: 'Auto-Update', cls: 'notif-kind-auto_update' };
      default:
        return { label: 'Novedad', cls: 'notif-kind-release' };
    }
  }

  function updateNotificationBadge() {
    const badge = $('badge-notifications');
    const btn = $('btn-notifications');
    const count = Number(state.unreadNotifications) || 0;

    if (badge) {
      if (count > 0) {
        badge.textContent = count > 99 ? '99+' : String(count);
        badge.classList.remove('hidden');
      } else {
        badge.textContent = '0';
        badge.classList.add('hidden');
      }
    }
    if (btn) {
      btn.classList.toggle('has-unread', count > 0);
    }

    const markAllBtn = $('btn-mark-all-read');
    if (markAllBtn) {
      markAllBtn.disabled = count === 0;
    }
  }

  function resolveNotifIconHtml(notif, app) {
    if (app) {
      return resolveIconHtml(app, 22);
    }
    if (notif && notif.icon) {
      const raw = String(notif.icon);
      if (raw.startsWith('/') || raw.startsWith('http')) {
        return `<img class="px-icon" src="${UI.escapeHtml(raw)}" width="22" height="22" alt="">`;
      }
      const clean = raw.replace(/\.png$/i, '');
      return UI.icon(clean, 22);
    }
    return UI.icon('ai', 22);
  }

  function renderNotificationsList() {
    const listEl = $('notifications-list');
    const emptyEl = $('notifications-empty');
    if (!listEl) return;

    const filter = state.notifFilter || 'all';
    document.querySelectorAll('#notif-filters [data-notif-filter]').forEach((tab) => {
      const isActive = tab.getAttribute('data-notif-filter') === filter;
      tab.classList.toggle('active', isActive);
      tab.setAttribute('aria-selected', isActive ? 'true' : 'false');
    });

    const filtered = state.notifications.filter((n) => {
      if (!n) return false;
      if (filter === 'all') return true;
      return String(n.kind || '').toLowerCase() === filter;
    });

    if (filtered.length === 0) {
      listEl.innerHTML = '';
      if (emptyEl) {
        emptyEl.classList.remove('hidden');
      }
      return;
    }

    if (emptyEl) {
      emptyEl.classList.add('hidden');
    }

    listEl.innerHTML = filtered
      .map((n) => {
        const notifId = String(n.id || '');
        const appId = n.app_id || n.appId || '';
        const app = appId ? state.apps.find((a) => a.id === appId) : null;
        const appStatus = app ? normalizeStatus(app) : '';
        const isUnread = !n.read;
        const kind = String(n.kind || 'release').toLowerCase();
        const kindMeta = getNotifKindMeta(kind);
        const appLabel = n.app_name || (app && app.name) || n.repo || 'NaSuite';
        const title = n.title || 'Nueva actualizacion disponible';
        const body = (n.body || n.message || n.notes || '').trim();
        const version = n.version || n.tag || (n.commit ? `#${shortSha(n.commit)}` : '');
        const ts = UI.formatRelativeTime(n.timestamp || n.created_at || n.date);
        const ghUrl = n.url || n.html_url || '';

        const actionBtns = [];
        if (app) {
          if (appStatus === 'update_available') {
            actionBtns.push(`
              <button type="button" class="btn btn-primary btn-xs" data-notif-action="update" data-app-id="${UI.escapeHtml(app.id)}" data-notif-id="${UI.escapeHtml(notifId)}">
                ${UI.icon('refresh', 13)}
                <span>Actualizar</span>
              </button>
            `);
          } else if (appStatus === 'not_installed') {
            actionBtns.push(`
              <button type="button" class="btn btn-primary btn-xs" data-notif-action="install" data-app-id="${UI.escapeHtml(app.id)}" data-notif-id="${UI.escapeHtml(notifId)}">
                ${UI.icon('download', 13)}
                <span>Instalar</span>
              </button>
            `);
          }
        }
        if (appId || ghUrl) {
          actionBtns.push(`
            <button type="button" class="btn btn-secondary btn-xs" data-notif-action="open-github" data-app-id="${UI.escapeHtml(appId)}" data-url="${UI.escapeHtml(ghUrl)}" data-notif-id="${UI.escapeHtml(notifId)}">
              ${UI.icon('catalog', 13)}
              <span>Ver en GitHub</span>
            </button>
          `);
        }

        return `
          <article class="notif-item ${isUnread ? 'unread' : ''}" data-notif-id="${UI.escapeHtml(notifId)}">
            <div class="notif-item-header">
              <div class="notif-item-app">
                ${resolveNotifIconHtml(n, app)}
                <span class="notif-item-app-name">${UI.escapeHtml(appLabel)}</span>
                <span class="notif-kind-badge ${kindMeta.cls}">${UI.escapeHtml(kindMeta.label)}</span>
                ${isUnread ? '<span class="notif-unread-dot" title="No leido"></span>' : ''}
              </div>
              <div class="notif-item-meta">
                <span class="notif-item-time">${UI.escapeHtml(ts)}</span>
              </div>
            </div>
            <div class="notif-item-title">${UI.escapeHtml(title)}</div>
            ${body ? `<div class="notif-item-body">${UI.escapeHtml(body)}</div>` : ''}
            <div class="notif-item-footer">
              ${version ? `<span class="notif-item-version">${UI.escapeHtml(version)}</span>` : '<span></span>'}
              <div class="notif-item-actions">${actionBtns.join('')}</div>
            </div>
          </article>
        `;
      })
      .join('');
  }

  async function loadNotifications() {
    try {
      const res = await API.getNotifications();
      const parsed = extractNotificationsPayload(res);
      state.notifications = parsed.items;
      state.unreadNotifications = parsed.unreadCount;
      updateNotificationBadge();
      if (state.notifModalOpen) {
        renderNotificationsList();
      }
      return state.notifications;
    } catch (_) {
      // Backend may not have notifications yet or transient error
      return state.notifications;
    }
  }

  async function markNotificationAsRead(notifId) {
    if (!notifId) return;
    const item = state.notifications.find((n) => String(n.id) === String(notifId));
    if (!item || item.read) return;

    item.read = true;
    state.unreadNotifications = Math.max(
      0,
      state.notifications.filter((n) => n && !n.read).length
    );
    updateNotificationBadge();
    if (state.notifModalOpen) {
      renderNotificationsList();
    }

    try {
      await API.markNotificationRead(notifId);
    } catch (_) {
      // Keep optimistic state; next loadNotifications() reconciles
    }
  }

  async function handleMarkAllNotificationsRead() {
    const btn = $('btn-mark-all-read');
    if (btn) btn.disabled = true;

    try {
      await API.markAllNotificationsRead();
      state.notifications = state.notifications.map((n) => (n ? { ...n, read: true } : n));
      state.unreadNotifications = 0;
      updateNotificationBadge();
      renderNotificationsList();
      UI.toast('Todas las novedades marcadas como leidas.', 'info');
    } catch (err) {
      UI.toast(`No se pudieron marcar como leidas: ${err.message}`, 'error');
      updateNotificationBadge();
    }
  }

  function openNotificationsModal() {
    const modal = $('modal-notifications');
    if (!modal) return;
    state.notifModalOpen = true;
    renderNotificationsList();
    UI.openModal(modal);
    loadNotifications();
  }

  function closeNotificationsModal() {
    state.notifModalOpen = false;
    UI.closeModal($('modal-notifications'));
    if (state.unreadNotifications > 0) {
      state.notifications = state.notifications.map((n) => (n ? { ...n, read: true } : n));
      state.unreadNotifications = 0;
      updateNotificationBadge();
      API.markAllNotificationsRead().catch(() => {});
    }
  }

  /* --------------------------------------------------------------------------
   * Add Repo Modal & Inline Add
   * ----------------------------------------------------------------------- */

  function openAddRepoModal() {
    const modal = $('modal-add-repo');
    const input = $('modal-repo-input');
    if (input) input.value = '';
    UI.openModal(modal);
    if (input) input.focus();
  }

  function closeAddRepoModal() {
    UI.closeModal($('modal-add-repo'));
  }

  function normalizeRepoInput(raw) {
    let val = String(raw || '').trim();
    if (!val) return '';
    val = val.replace(/^https?:\/\/(www\.)?github\.com\//i, '');
    val = val.replace(/\.git$/i, '');
    val = val.replace(/^\/+|\/+$/g, '');
    const parts = val.split('/').filter(Boolean);
    if (parts.length >= 2) {
      return `${parts[0]}/${parts[1]}`;
    }
    return val;
  }

  async function handleAddRepo(rawInput, source = 'modal') {
    const repo = normalizeRepoInput(rawInput);
    if (!repo || !repo.includes('/')) {
      UI.toast('Ingresa un repositorio valido en formato usuario/repo o URL de GitHub.', 'warning');
      return;
    }

    const btn = source === 'modal' ? $('btn-confirm-add-repo') : $('btn-add-custom-repo-inline');
    if (btn) btn.disabled = true;

    try {
      await API.addRepo(repo);
      UI.toast(`Repositorio ${repo} agregado correctamente.`, 'success');
      if (source === 'modal') {
        closeAddRepoModal();
      } else {
        const inlineInput = $('input-custom-repo');
        if (inlineInput) inlineInput.value = '';
      }
      await loadConfig();
      await loadApps(true);
    } catch (err) {
      UI.toast(`No se pudo agregar ${repo}: ${err.message}`, 'error');
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  async function handleRemoveRepo(repoSlug) {
    const ok = await UI.confirm(
      'Quitar repositorio',
      `Deseas quitar "${repoSlug}" de la lista de repositorios personalizados? No se eliminaran archivos locales.`
    );
    if (!ok) return;

    try {
      await API.removeRepo(repoSlug);
      UI.toast(`Repositorio ${repoSlug} removido.`, 'info');
      await loadConfig();
      await loadApps(true);
    } catch (err) {
      UI.toast(`Error al quitar repositorio: ${err.message}`, 'error');
    }
  }

  /* --------------------------------------------------------------------------
   * App Actions & Background Task Polling
   * ----------------------------------------------------------------------- */

  function startTaskPolling(appId, taskId, type = 'install') {
    if (!appId || !taskId) return;

    const existing = state.activeTasks.get(appId);
    if (existing && existing.timerId) {
      clearInterval(existing.timerId);
    }

    const taskState = {
      taskId,
      type,
      progress: 5,
      status: 'running',
      message: type === 'update' ? 'Iniciando actualizacion...' : 'Iniciando descarga...',
      timerId: null,
    };
    state.activeTasks.set(appId, taskState);
    renderDashboard();

    const poll = async () => {
      try {
        const res = await API.getTask(taskId);
        const task = extractTaskPayload(res);
        if (!task) return;

        const st = String(task.status || '').toLowerCase();
        taskState.progress = Number(task.progress ?? taskState.progress) || 0;
        taskState.message = task.message || task.step || taskState.message;
        taskState.status = st;

        updateCardProgressDom(appId, taskState);

        if (st === 'done' || st === 'completed' || st === 'success') {
          stopTaskPolling(appId);
          const appObj = state.apps.find((a) => a.id === appId);
          const appName = (appObj && appObj.name) || appId;
          UI.toast(
            type === 'update'
              ? `${appName} actualizada correctamente.`
              : `${appName} instalada correctamente.`,
            'success'
          );
          await loadApps(true);
        } else if (st === 'error' || st === 'failed') {
          stopTaskPolling(appId);
          const errMsg = task.error || task.message || 'Error desconocido en la tarea.';
          UI.toast(`Fallo en ${type === 'update' ? 'actualizacion' : 'instalacion'}: ${errMsg}`, 'error');
          openConsoleDrawer();
          await fetchLogs();
          await loadApps(true);
        }
      } catch (err) {
        // Keep polling on transient error unless 404
        if (err.status === 404) {
          stopTaskPolling(appId);
          await loadApps(true);
        }
      }
    };

    taskState.timerId = setInterval(poll, 700);
    poll();
  }

  function stopTaskPolling(appId) {
    const existing = state.activeTasks.get(appId);
    if (existing && existing.timerId) {
      clearInterval(existing.timerId);
    }
    state.activeTasks.delete(appId);
  }

  function updateCardProgressDom(appId, taskState) {
    const card = document.querySelector(`.app-card[data-app-id="${CSS.escape(appId)}"]`);
    if (!card) return;
    const fill = card.querySelector('.card-progress-fill');
    const pctEl = card.querySelector('.card-progress-pct');
    const textEl = card.querySelector('.card-progress-text');
    const pct = Math.max(0, Math.min(100, Number(taskState.progress) || 0));

    if (fill) fill.style.width = `${pct}%`;
    if (pctEl) pctEl.textContent = `${Math.round(pct)}%`;
    if (textEl) textEl.textContent = taskState.message || 'Procesando...';
  }

  async function resumeActiveTasks() {
    try {
      const res = await API.getTasks();
      const raw = res && res.tasks ? res.tasks : res;
      const list = Array.isArray(raw)
        ? raw
        : raw && typeof raw === 'object'
        ? Object.values(raw)
        : [];

      for (const task of list) {
        if (!task) continue;
        const st = String(task.status || '').toLowerCase();
        if (st === 'running' || st === 'queued' || st === 'pending') {
          const appId = task.app_id || task.appId;
          const taskId = task.id || task.task_id;
          if (appId && taskId && !state.activeTasks.has(appId)) {
            startTaskPolling(appId, taskId, task.type || task.action || 'install');
          }
        }
      }
    } catch (_) {
      // Non-fatal on startup
    }
  }

  async function handleAppAction(action, appId) {
    const app = state.apps.find((a) => a.id === appId);
    const appName = (app && app.name) || appId;

    if (action === 'details') {
      openAppDetailModal(appId);
      return;
    }

    if (action === 'open-folder') {
      try {
        await API.openFolder(appId);
      } catch (err) {
        UI.toast(`No se pudo abrir la carpeta: ${err.message}`, 'error');
      }
      return;
    }

    if (action === 'open-github') {
      try {
        await API.openGithub(appId);
      } catch (err) {
        UI.toast(`No se pudo abrir GitHub: ${err.message}`, 'error');
      }
      return;
    }

    if (action === 'install') {
      try {
        const res = await API.installApp(appId, 'auto');
        const taskId = res.task_id || (res.task && res.task.id) || res.id;
        if (taskId) {
          UI.toast(`Descargando e instalando ${appName}...`, 'info');
          startTaskPolling(appId, taskId, 'install');
        } else {
          UI.toast(`${appName} instalada correctamente.`, 'success');
          await loadApps(true);
        }
      } catch (err) {
        UI.toast(`Error al instalar ${appName}: ${err.message}`, 'error');
      }
      return;
    }

    if (action === 'update') {
      try {
        const res = await API.updateApp(appId);
        const taskId = res.task_id || (res.task && res.task.id) || res.id;
        if (taskId) {
          UI.toast(`Actualizando ${appName}...`, 'info');
          startTaskPolling(appId, taskId, 'update');
        } else {
          UI.toast(`${appName} actualizada correctamente.`, 'success');
          await loadApps(true);
        }
      } catch (err) {
        UI.toast(`Error al actualizar ${appName}: ${err.message}`, 'error');
      }
      return;
    }

    if (action === 'launch') {
      state.busyApps.add(appId);
      renderDashboard();
      try {
        await API.launchApp(appId);
        UI.toast(`Iniciando ${appName}...`, 'success');
        await loadApps(true);
      } catch (err) {
        UI.toast(`No se pudo iniciar ${appName}: ${err.message}`, 'error');
      } finally {
        state.busyApps.delete(appId);
        renderDashboard();
      }
      return;
    }

    if (action === 'stop') {
      state.busyApps.add(appId);
      renderDashboard();
      try {
        await API.stopApp(appId);
        UI.toast(`${appName} detenida.`, 'info');
        await loadApps(true);
      } catch (err) {
        UI.toast(`No se pudo detener ${appName}: ${err.message}`, 'error');
      } finally {
        state.busyApps.delete(appId);
        renderDashboard();
      }
      return;
    }

    if (action === 'uninstall') {
      const ok = await UI.confirm(
        `Desinstalar ${appName}`,
        `Esta accion eliminara la instalacion administrada de "${appName}". Podras volver a descargarla desde GitHub en cualquier momento.`
      );
      if (!ok) return;

      state.busyApps.add(appId);
      renderDashboard();
      try {
        await API.uninstallApp(appId);
        UI.toast(`${appName} desinstalada correctamente.`, 'info');
        await loadApps(true);
      } catch (err) {
        UI.toast(`Error al desinstalar ${appName}: ${err.message}`, 'error');
      } finally {
        state.busyApps.delete(appId);
        renderDashboard();
      }
    }
  }

  /* --------------------------------------------------------------------------
   * Data Loading & GitHub Sync
   * ----------------------------------------------------------------------- */

  async function loadConfig() {
    try {
      const res = await API.getConfig();
      state.config = extractConfigPayload(res);
      if (state.config.theme) {
        UI.setTheme(state.config.theme);
      }
      updateSidebarBadgesAndStats();
      if (state.activeView === 'settings') {
        renderSettingsView();
      }
      return state.config;
    } catch (err) {
      UI.toast(`No se pudo cargar la configuracion: ${err.message}`, 'warning');
      return {};
    }
  }

  async function loadApps(rerender = true) {
    try {
      const res = await API.getApps();
      const parsed = extractAppsPayload(res);
      state.apps = parsed.apps;
      if (parsed.lastSync) state.lastSync = parsed.lastSync;
      if (parsed.suiteVersion) state.suiteVersion = parsed.suiteVersion;
      if (parsed.githubUser && !state.config.github_user) {
        state.config.github_user = parsed.githubUser;
      }

      updateSidebarBadgesAndStats();

      if (rerender) {
        if (
          state.activeView === 'dashboard' ||
          state.activeView === 'installed' ||
          state.activeView === 'updates'
        ) {
          renderDashboard();
        } else if (state.activeView === 'repos') {
          renderReposView();
        }
        if (state.detailAppId) {
          openAppDetailModal(state.detailAppId);
        }
      }
      return state.apps;
    } catch (err) {
      if (rerender) {
        UI.toast(`Error al cargar aplicaciones: ${err.message}`, 'error');
      }
      return state.apps;
    }
  }

  async function triggerSync(silent = false) {
    if (state.syncing) return;
    state.syncing = true;
    setSyncStatus('Sincronizando con GitHub...', true);

    try {
      const res = await API.syncRepos();
      state.lastSync = (res && (res.last_sync || res.synced_at)) || new Date().toISOString();
      await loadApps(true);
      await loadNotifications();
      setSyncStatus(`Sincronizado (${UI.formatRelativeTime(state.lastSync)})`, false);
      if (!silent) {
        UI.toast('Repositorios sincronizados con GitHub.', 'success');
      }
    } catch (err) {
      setSyncStatus('Error de sincronizacion', false);
      if (!silent) {
        UI.toast(`Error al sincronizar con GitHub: ${err.message}`, 'error');
      }
    } finally {
      state.syncing = false;
      updateSidebarBadgesAndStats();
    }
  }

  /* --------------------------------------------------------------------------
   * Console Drawer
   * ----------------------------------------------------------------------- */

  function openConsoleDrawer() {
    const drawer = $('console-drawer');
    if (!drawer) return;
    state.consoleOpen = true;
    drawer.classList.remove('hidden');
    drawer.classList.add('open', 'active');
    const toggleBtn = $('btn-toggle-console');
    if (toggleBtn) toggleBtn.classList.add('active');

    fetchLogs();
    if (!state.consolePollId) {
      state.consolePollId = setInterval(() => {
        if (state.consoleOpen && !document.hidden) {
          fetchLogs();
        }
      }, 2000);
    }
  }

  function closeConsoleDrawer() {
    const drawer = $('console-drawer');
    if (!drawer) return;
    state.consoleOpen = false;
    drawer.classList.add('hidden');
    drawer.classList.remove('open', 'active');
    const toggleBtn = $('btn-toggle-console');
    if (toggleBtn) toggleBtn.classList.remove('active');

    if (state.consolePollId) {
      clearInterval(state.consolePollId);
      state.consolePollId = null;
    }
  }

  function toggleConsoleDrawer() {
    if (state.consoleOpen) {
      closeConsoleDrawer();
    } else {
      openConsoleDrawer();
    }
  }

  async function fetchLogs() {
    const output = $('console-output');
    if (!output) return;

    try {
      const res = await API.getLogs();
      const entries = Array.isArray(res) ? res : Array.isArray(res && res.logs) ? res.logs : [];

      const wasAtBottom =
        output.scrollHeight - output.scrollTop - output.clientHeight < 40;

      if (entries.length === 0) {
        output.innerHTML = `<div class="console-empty">Sin registros todavia.</div>`;
        return;
      }

      output.innerHTML = entries
        .map((entry) => {
          if (typeof entry === 'string') {
            return `<div class="log-line log-info">${UI.escapeHtml(entry)}</div>`;
          }
          const level = String(entry.level || 'info').toLowerCase();
          const ts = entry.timestamp || entry.time || '';
          const src = entry.source ? `[${entry.source}] ` : '';
          const msg = entry.message || entry.text || '';
          return `<div class="log-line log-${UI.escapeHtml(level)}"><span class="log-ts">${UI.escapeHtml(ts)}</span> <span class="log-level">[${UI.escapeHtml(level.toUpperCase())}]</span> <span class="log-msg">${UI.escapeHtml(src + msg)}</span></div>`;
        })
        .join('');

      if (wasAtBottom) {
        output.scrollTop = output.scrollHeight;
      }
    } catch (_) {
      // Ignore transient log polling failures
    }
  }

  async function handleClearConsole() {
    try {
      await API.clearLogs();
      const output = $('console-output');
      if (output) {
        output.innerHTML = `<div class="console-empty">Consola limpiada.</div>`;
      }
    } catch (err) {
      UI.toast(`No se pudo limpiar la consola: ${err.message}`, 'error');
    }
  }

  /* --------------------------------------------------------------------------
   * Periodic Auto-Refresh & Auto-Sync
   * ----------------------------------------------------------------------- */

  function setupAppPolling() {
    if (state.appPollId) clearInterval(state.appPollId);
    state.appPollId = setInterval(() => {
      if (!document.hidden && state.activeTasks.size === 0) {
        loadApps(true);
        loadNotifications();
      }
    }, 15000);
  }

  function setupAutoSyncTimer() {
    if (state.autoSyncId) {
      clearInterval(state.autoSyncId);
      state.autoSyncId = null;
    }
    const mins = Number(state.config.auto_sync_minutes) || 0;
    if (mins > 0) {
      state.autoSyncId = setInterval(() => {
        if (!document.hidden && !state.syncing) {
          triggerSync(true);
        }
      }, mins * 60 * 1000);
    }
  }

  /* --------------------------------------------------------------------------
   * Event Binding
   * ----------------------------------------------------------------------- */

  function bindEvents() {
    // Topbar actions
    $('btn-sync')?.addEventListener('click', () => triggerSync(false));
    $('btn-add-repo')?.addEventListener('click', openAddRepoModal);
    $('btn-notifications')?.addEventListener('click', openNotificationsModal);
    $('btn-toggle-console')?.addEventListener('click', toggleConsoleDrawer);
    $('btn-theme-quick')?.addEventListener('click', cycleQuickTheme);

    // Search input
    const searchInput = $('search-input');
    if (searchInput) {
      searchInput.addEventListener('input', (ev) => {
        state.searchQuery = ev.target.value || '';
        if (
          state.activeView !== 'dashboard' &&
          state.activeView !== 'installed' &&
          state.activeView !== 'updates'
        ) {
          switchView('dashboard');
        } else {
          renderDashboard();
        }
      });
    }

    // Sort select
    const sortSelect = $('sort-select');
    if (sortSelect) {
      sortSelect.addEventListener('change', (ev) => {
        state.sortBy = ev.target.value || 'status';
        renderDashboard();
      });
    }

    // Sidebar navigation
    document.querySelectorAll('[data-view]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const view = btn.getAttribute('data-view');
        if (view) switchView(view);
      });
    });

    // Category chips delegation
    $('category-chips')?.addEventListener('click', (ev) => {
      const chip = ev.target.closest('[data-category]');
      if (!chip) return;
      state.selectedCategory = chip.getAttribute('data-category') || 'all';
      renderDashboard();
    });

    // App cards action delegation
    $('apps-grid')?.addEventListener('click', (ev) => {
      const btn = ev.target.closest('[data-action]');
      if (!btn) return;
      const action = btn.getAttribute('data-action');
      const appId = btn.getAttribute('data-app-id');
      if (action && appId) {
        handleAppAction(action, appId);
      }
    });

    // Repos table delegation
    $('repos-table-body')?.addEventListener('click', (ev) => {
      const btn = ev.target.closest('[data-repo-action]');
      if (!btn) return;
      const action = btn.getAttribute('data-repo-action');
      const appId = btn.getAttribute('data-app-id');
      const slug = btn.getAttribute('data-repo-slug');

      if (action === 'remove-repo' && slug) {
        handleRemoveRepo(slug);
      } else if (action && appId) {
        handleAppAction(action, appId);
      }
    });

    // Inline add custom repo
    $('btn-add-custom-repo-inline')?.addEventListener('click', () => {
      handleAddRepo($('input-custom-repo')?.value, 'inline');
    });
    $('input-custom-repo')?.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter') {
        ev.preventDefault();
        handleAddRepo(ev.target.value, 'inline');
      }
    });

    // Add repo modal
    $('btn-confirm-add-repo')?.addEventListener('click', () => {
      handleAddRepo($('modal-repo-input')?.value, 'modal');
    });
    $('modal-repo-input')?.addEventListener('keydown', (ev) => {
      if (ev.key === 'Enter') {
        ev.preventDefault();
        handleAddRepo(ev.target.value, 'modal');
      }
    });
    $('btn-close-add-repo')?.addEventListener('click', closeAddRepoModal);
    $('modal-add-repo')?.addEventListener('click', (ev) => {
      if (ev.target === $('modal-add-repo')) closeAddRepoModal();
    });

    // Detail modal
    $('btn-close-detail')?.addEventListener('click', closeAppDetailModal);
    $('modal-app-detail')?.addEventListener('click', (ev) => {
      if (ev.target === $('modal-app-detail')) closeAppDetailModal();
    });
    $('detail-actions')?.addEventListener('click', (ev) => {
      const btn = ev.target.closest('[data-detail-action]');
      if (!btn) return;
      const action = btn.getAttribute('data-detail-action');
      const appId = btn.getAttribute('data-app-id');
      if (action && appId) {
        if (action === 'install' || action === 'update' || action === 'launch') {
          closeAppDetailModal();
        }
        handleAppAction(action, appId);
      }
    });

    // Settings theme picker & save button
    $('theme-grid')?.addEventListener('click', (ev) => {
      const card = ev.target.closest('[data-theme-id]');
      if (!card) return;
      const themeId = card.getAttribute('data-theme-id');
      if (themeId) applyThemeSelection(themeId, false);
    });
    $('btn-save-config')?.addEventListener('click', handleSaveConfig);

    // Console drawer controls
    $('btn-close-console')?.addEventListener('click', closeConsoleDrawer);
    $('btn-clear-console')?.addEventListener('click', handleClearConsole);

    // Notifications modal
    $('btn-close-notifications')?.addEventListener('click', closeNotificationsModal);
    $('btn-mark-all-read')?.addEventListener('click', handleMarkAllNotificationsRead);
    $('modal-notifications')?.addEventListener('click', (ev) => {
      if (ev.target === $('modal-notifications')) closeNotificationsModal();
    });
    $('notif-filters')?.addEventListener('click', (ev) => {
      const tab = ev.target.closest('[data-notif-filter]');
      if (!tab) return;
      state.notifFilter = tab.getAttribute('data-notif-filter') || 'all';
      renderNotificationsList();
    });
    $('notifications-list')?.addEventListener('click', (ev) => {
      const actionBtn = ev.target.closest('[data-notif-action]');
      if (actionBtn) {
        ev.stopPropagation();
        const action = actionBtn.getAttribute('data-notif-action');
        const appId = actionBtn.getAttribute('data-app-id') || '';
        const notifId = actionBtn.getAttribute('data-notif-id') || '';
        const url = actionBtn.getAttribute('data-url') || '';
        if (notifId) markNotificationAsRead(notifId);

        if (action === 'update' && appId) {
          closeNotificationsModal();
          handleUpdate(appId);
        } else if (action === 'install' && appId) {
          closeNotificationsModal();
          handleInstall(appId);
        } else if (action === 'open-github') {
          openGithub(appId, url);
        }
        return;
      }

      const card = ev.target.closest('.notif-item[data-notif-id]');
      if (card && card.classList.contains('unread')) {
        const notifId = card.getAttribute('data-notif-id');
        if (notifId) markNotificationAsRead(notifId);
      }
    });

    // Global Escape key handler for modals
    document.addEventListener('keydown', (ev) => {
      if (ev.key === 'Escape') {
        if (state.detailAppId) closeAppDetailModal();
        if (state.notifModalOpen) closeNotificationsModal();
        const addModal = $('modal-add-repo');
        if (addModal && !addModal.classList.contains('hidden')) {
          closeAddRepoModal();
        }
      }
    });
  }

  /* --------------------------------------------------------------------------
   * Startup Sequence
   * ----------------------------------------------------------------------- */

  async function init() {
    bindEvents();

    UI.splashProgress(15, 'Cargando configuracion...');
    await loadConfig();

    UI.splashProgress(50, 'Cargando aplicaciones locales...');
    await loadApps(true);
    await loadNotifications();
    await resumeActiveTasks();

    UI.splashProgress(85, 'Preparando interfaz...');
    switchView('dashboard');
    setupAppPolling();
    setupAutoSyncTimer();

    UI.splashProgress(100, 'Listo');
    setTimeout(() => {
      UI.hideSplash();
    }, 180);

    // Background GitHub sync immediately after splash clears
    triggerSync(true);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})(window);
