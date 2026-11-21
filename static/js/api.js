/**
 * NaSuite Launcher — REST API Client
 * Exposes window.NaSuiteAPI for communicating with the local Python backend.
 */
(function (global) {
  'use strict';

  const BASE = '';

  async function request(method, path, body = null) {
    const options = {
      method,
      headers: {
        Accept: 'application/json',
      },
      cache: 'no-store',
    };

    if (body !== null && body !== undefined) {
      options.headers['Content-Type'] = 'application/json';
      options.body = JSON.stringify(body);
    }

    let response;
    try {
      response = await fetch(BASE + path, options);
    } catch (networkErr) {
      const err = new Error('No se pudo conectar con el servidor local de NaSuite.');
      err.cause = networkErr;
      err.isNetworkError = true;
      throw err;
    }

    const contentType = response.headers.get('content-type') || '';
    let data = null;
    if (contentType.includes('application/json')) {
      try {
        data = await response.json();
      } catch (_) {
        data = null;
      }
    } else {
      const text = await response.text().catch(() => '');
      try {
        data = text ? JSON.parse(text) : null;
      } catch (_) {
        data = { message: text };
      }
    }

    if (!response.ok || (data && data.ok === false)) {
      const message =
        (data && (data.error || data.message || data.detail)) ||
        `Error HTTP ${response.status} al consultar ${path}`;
      const err = new Error(message);
      err.status = response.status;
      err.data = data;
      throw err;
    }

    return data;
  }

  const NaSuiteAPI = {
    health() {
      return request('GET', '/api/health');
    },

    getApps() {
      return request('GET', '/api/apps');
    },

    syncRepos() {
      return request('POST', '/api/sync');
    },

    launchApp(id) {
      return request('POST', `/api/apps/${encodeURIComponent(id)}/launch`);
    },

    stopApp(id) {
      return request('POST', `/api/apps/${encodeURIComponent(id)}/stop`);
    },

    installApp(id, mode = 'auto') {
      return request('POST', `/api/apps/${encodeURIComponent(id)}/install`, { mode });
    },

    updateApp(id) {
      return request('POST', `/api/apps/${encodeURIComponent(id)}/update`);
    },

    uninstallApp(id) {
      return request('POST', `/api/apps/${encodeURIComponent(id)}/uninstall`);
    },

    openFolder(id) {
      return request('POST', `/api/apps/${encodeURIComponent(id)}/open-folder`);
    },

    openGithub(id) {
      return request('POST', `/api/apps/${encodeURIComponent(id)}/open-github`);
    },

    getTasks() {
      return request('GET', '/api/tasks');
    },

    getTask(taskId) {
      return request('GET', `/api/tasks/${encodeURIComponent(taskId)}`);
    },

    getLogs() {
      return request('GET', '/api/logs');
    },

    clearLogs() {
      return request('POST', '/api/logs/clear');
    },

    getConfig() {
      return request('GET', '/api/config');
    },

    saveConfig(cfg) {
      return request('POST', '/api/config', cfg);
    },

    addRepo(repo) {
      return request('POST', '/api/repos/add', { repo });
    },

    removeRepo(repo) {
      return request('POST', '/api/repos/remove', { repo });
    },

    getNotifications() {
      return request('GET', '/api/notifications');
    },

    markNotificationRead(id) {
      return request('POST', '/api/notifications/read', { id });
    },

    markAllNotificationsRead() {
      return request('POST', '/api/notifications/read', { all: true });
    },

    hideWindow() {
      return request('POST', '/api/window/hide');
    },

    setAutostart(enabled) {
      return request('POST', '/api/system/autostart', { enabled: Boolean(enabled) });
    },
  };

  global.NaSuiteAPI = NaSuiteAPI;
})(window);
