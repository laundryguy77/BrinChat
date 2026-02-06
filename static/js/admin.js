/**
 * BrinChat Admin Portal
 */

// State
let currentUser = null;
let usersPage = 1;
let auditPage = 1;
let userSearchTimeout = null;

// API helpers
async function api(endpoint, options = {}) {
    const token = localStorage.getItem('token');
    if (!token) {
        window.location.href = '/';
        return null;
    }

    const response = await fetch(`/api/admin${endpoint}`, {
        ...options,
        headers: {
            'Authorization': `Bearer ${token}`,
            'Content-Type': 'application/json',
            ...options.headers
        }
    });

    if (response.status === 401) {
        localStorage.removeItem('token');
        window.location.href = '/';
        return null;
    }

    if (response.status === 403) {
        showToast('Admin access required', 'error');
        window.location.href = '/';
        return null;
    }

    if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
        throw new Error(error.detail || 'Request failed');
    }

    return response.json();
}

// Toast notifications
function showToast(message, type = 'info') {
    const container = document.getElementById('toast-container');
    const toast = document.createElement('div');
    toast.className = `toast toast-${type}`;
    toast.textContent = message;
    container.appendChild(toast);

    setTimeout(() => {
        toast.style.opacity = '0';
        toast.style.transform = 'translateX(100%)';
        setTimeout(() => toast.remove(), 300);
    }, 3000);
}

// Modal helpers
function showModal(id) {
    document.getElementById(id).classList.remove('hidden');
}

function hideModal(id) {
    document.getElementById(id).classList.add('hidden');
}

// Initialize
async function init() {
    // Verify admin access
    try {
        const response = await fetch('/api/auth/me', {
            headers: {
                'Authorization': `Bearer ${localStorage.getItem('token')}`
            }
        });

        if (!response.ok) {
            window.location.href = '/';
            return;
        }

        currentUser = await response.json();
        document.getElementById('admin-username').textContent = currentUser.username;

        // Check admin status
        const adminCheck = await api('/dashboard').catch(() => null);
        if (!adminCheck) {
            showToast('Admin access required', 'error');
            window.location.href = '/';
            return;
        }

    } catch (e) {
        window.location.href = '/';
        return;
    }

    // Set up tab navigation
    document.querySelectorAll('.admin-nav-item').forEach(item => {
        item.addEventListener('click', () => switchTab(item.dataset.tab));
    });

    // Load initial data
    loadDashboard();
}

function logout() {
    localStorage.removeItem('token');
    window.location.href = '/';
}

// Tab switching
function switchTab(tabName) {
    // Update nav
    document.querySelectorAll('.admin-nav-item').forEach(item => {
        item.classList.toggle('active', item.dataset.tab === tabName);
    });

    // Update content
    document.querySelectorAll('.tab-content').forEach(content => {
        content.classList.toggle('active', content.id === `tab-${tabName}`);
    });

    // Load tab data
    switch (tabName) {
        case 'dashboard':
            loadDashboard();
            break;
        case 'users':
            loadUsers();
            break;
        case 'features':
            loadFeatures();
            break;
        case 'themes':
            loadThemes();
            break;
        case 'audit':
            loadAuditLog();
            break;
    }
}

// Dashboard
async function loadDashboard() {
    try {
        const data = await api('/dashboard');

        const statsHtml = `
            <div class="stat-card">
                <div class="stat-value">${data.users.total}</div>
                <div class="stat-label">Total Users</div>
                <div class="text-xs text-green-400 mt-2">+${data.users.recent_signups} this week</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">${data.users.active}</div>
                <div class="stat-label">Active Users</div>
                <div class="text-xs text-slate-400 mt-2">${data.users.admins} admins</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">${data.conversations.total}</div>
                <div class="stat-label">Conversations</div>
                <div class="text-xs text-slate-400 mt-2">${data.conversations.total_messages} messages</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">${data.memories.total}</div>
                <div class="stat-label">Memories</div>
                <div class="text-xs text-slate-400 mt-2">${data.memories.users_with_memories} users</div>
            </div>
        `;
        document.getElementById('dashboard-stats').innerHTML = statsHtml;

        // Load recent audit activity
        const auditData = await api('/audit-log?page_size=5');
        const activityHtml = auditData.entries.length > 0
            ? auditData.entries.map(e => `
                <div class="flex items-center justify-between py-2 border-b border-slate-700/50 last:border-0">
                    <div>
                        <span class="text-white">${e.admin_username || 'Unknown'}</span>
                        <span class="text-slate-400">${formatAction(e.action)}</span>
                        <span class="text-slate-300">${e.target_type}: ${e.target_id || '-'}</span>
                    </div>
                    <span class="text-xs text-slate-500">${formatDate(e.created_at)}</span>
                </div>
            `).join('')
            : '<p class="text-slate-500">No recent activity</p>';
        document.getElementById('recent-activity').innerHTML = activityHtml;

    } catch (e) {
        showToast('Failed to load dashboard: ' + e.message, 'error');
    }
}

// Users
async function loadUsers() {
    try {
        const search = document.getElementById('user-search').value;
        const includeInactive = document.getElementById('include-inactive').checked;

        const params = new URLSearchParams({
            page: usersPage,
            page_size: 20,
            include_inactive: includeInactive
        });
        if (search) params.append('search', search);

        const data = await api(`/users?${params}`);

        const tbody = document.getElementById('users-table-body');
        tbody.innerHTML = data.users.map(user => `
            <tr>
                <td class="text-slate-400">${user.id}</td>
                <td class="font-medium">${escapeHtml(user.username)}</td>
                <td class="text-slate-400">${user.email || '-'}</td>
                <td>
                    <span class="badge ${user.is_active ? 'badge-success' : 'badge-danger'}">
                        ${user.is_active ? 'Active' : 'Inactive'}
                    </span>
                </td>
                <td>
                    <span class="badge ${user.is_admin ? 'badge-info' : 'badge-warning'}">
                        ${user.is_admin ? 'Admin' : 'User'}
                    </span>
                    ${user.mode_restriction ? `<span class="badge badge-danger ml-1">${user.mode_restriction}</span>` : ''}
                </td>
                <td class="text-slate-400 text-xs">${formatDate(user.created_at)}</td>
                <td>
                    <div class="flex gap-2">
                        <button onclick="editUser(${user.id})" class="text-slate-400 hover:text-white transition-colors">
                            <span class="material-symbols-outlined text-sm">edit</span>
                        </button>
                        <button onclick="confirmDeleteUser(${user.id}, '${escapeHtml(user.username)}')" class="text-slate-400 hover:text-red-400 transition-colors">
                            <span class="material-symbols-outlined text-sm">delete</span>
                        </button>
                    </div>
                </td>
            </tr>
        `).join('');

        // Pagination
        const pagination = document.getElementById('users-pagination');
        pagination.innerHTML = `
            <span class="text-sm text-slate-400">
                Showing ${(usersPage - 1) * 20 + 1}-${Math.min(usersPage * 20, data.total)} of ${data.total}
            </span>
            <div class="flex gap-2">
                <button onclick="usersPage--; loadUsers()" class="btn btn-secondary btn-sm" ${usersPage === 1 ? 'disabled' : ''}>
                    Previous
                </button>
                <button onclick="usersPage++; loadUsers()" class="btn btn-secondary btn-sm" ${usersPage >= data.total_pages ? 'disabled' : ''}>
                    Next
                </button>
            </div>
        `;

    } catch (e) {
        showToast('Failed to load users: ' + e.message, 'error');
    }
}

function debounceUserSearch() {
    clearTimeout(userSearchTimeout);
    userSearchTimeout = setTimeout(() => {
        usersPage = 1;
        loadUsers();
    }, 300);
}

function showCreateUserModal() {
    document.getElementById('create-user-form').reset();
    showModal('create-user-modal');
}

async function createUser(event) {
    event.preventDefault();
    const form = event.target;
    const data = {
        username: form.username.value,
        password: form.password.value,
        is_admin: form.is_admin.checked
    };

    try {
        await api('/users', {
            method: 'POST',
            body: JSON.stringify(data)
        });
        hideModal('create-user-modal');
        showToast('User created successfully', 'success');
        loadUsers();
    } catch (e) {
        showToast('Failed to create user: ' + e.message, 'error');
    }
}

async function editUser(userId) {
    try {
        const user = await api(`/users/${userId}`);
        const features = await api(`/users/${userId}/features`);

        document.getElementById('edit-user-id').value = userId;
        document.getElementById('edit-username').value = user.username;
        document.getElementById('edit-is-active').value = user.is_active.toString();
        document.getElementById('edit-is-admin').value = user.is_admin.toString();
        document.getElementById('edit-mode-restriction').value = user.mode_restriction || '';

        // Feature overrides
        const overridesContainer = document.getElementById('user-feature-overrides');
        overridesContainer.innerHTML = Object.entries(features.features).map(([key, value]) => `
            <div class="flex items-center justify-between">
                <span class="text-sm">${value.display_name}</span>
                <select data-feature="${key}" class="form-input w-32 text-xs py-1" onchange="setFeatureOverride(${userId}, '${key}', this.value)">
                    <option value="default" ${value.override === null ? 'selected' : ''}>Default (${value.default ? 'On' : 'Off'})</option>
                    <option value="true" ${value.override === true ? 'selected' : ''}>On</option>
                    <option value="false" ${value.override === false ? 'selected' : ''}>Off</option>
                </select>
            </div>
        `).join('');

        showModal('edit-user-modal');
    } catch (e) {
        showToast('Failed to load user: ' + e.message, 'error');
    }
}

async function updateUser(event) {
    event.preventDefault();
    const form = event.target;
    const userId = form.user_id.value;

    const data = {
        is_active: form.is_active.value === 'true',
        is_admin: form.is_admin.value === 'true',
        mode_restriction: form.mode_restriction.value || null
    };

    try {
        await api(`/users/${userId}`, {
            method: 'PATCH',
            body: JSON.stringify(data)
        });
        hideModal('edit-user-modal');
        showToast('User updated successfully', 'success');
        loadUsers();
    } catch (e) {
        showToast('Failed to update user: ' + e.message, 'error');
    }
}

async function setFeatureOverride(userId, featureKey, value) {
    try {
        const enabled = value === 'default' ? null : value === 'true';
        await api(`/users/${userId}/features/${featureKey}`, {
            method: 'PUT',
            body: JSON.stringify({ enabled })
        });
        showToast('Feature override updated', 'success');
    } catch (e) {
        showToast('Failed to update feature: ' + e.message, 'error');
    }
}

function confirmDeleteUser(userId, username) {
    document.getElementById('confirm-delete-message').textContent =
        `Are you sure you want to delete user "${username}"? This will delete all their data including conversations, memories, and settings. This action cannot be undone.`;

    document.getElementById('confirm-delete-btn').onclick = () => deleteUser(userId);
    showModal('confirm-delete-modal');
}

async function deleteUser(userId) {
    try {
        await api(`/users/${userId}`, { method: 'DELETE' });
        hideModal('confirm-delete-modal');
        showToast('User deleted successfully', 'success');
        loadUsers();
    } catch (e) {
        showToast('Failed to delete user: ' + e.message, 'error');
    }
}

function confirmResetPassword() {
    const userId = document.getElementById('edit-user-id').value;
    document.getElementById('reset-password-user-id').value = userId;
    document.getElementById('reset-password-form').reset();
    hideModal('edit-user-modal');
    showModal('reset-password-modal');
}

async function resetPassword(event) {
    event.preventDefault();
    const form = event.target;

    if (form.new_password.value !== form.confirm_password.value) {
        showToast('Passwords do not match', 'error');
        return;
    }

    try {
        await api(`/users/${form.user_id.value}/reset-password`, {
            method: 'POST',
            body: JSON.stringify({ new_password: form.new_password.value })
        });
        hideModal('reset-password-modal');
        showToast('Password reset successfully', 'success');
    } catch (e) {
        showToast('Failed to reset password: ' + e.message, 'error');
    }
}

// Features
async function loadFeatures() {
    try {
        const data = await api('/features');

        // Group by category
        const byCategory = {};
        data.features.forEach(f => {
            if (!byCategory[f.category]) byCategory[f.category] = [];
            byCategory[f.category].push(f);
        });

        const html = Object.entries(byCategory).map(([category, features]) => `
            <div class="bg-surface-dark rounded-xl p-4 border border-slate-700/50">
                <h3 class="text-sm font-medium text-slate-300 uppercase tracking-wider mb-4">${category}</h3>
                <div class="space-y-3">
                    ${features.map(f => `
                        <div class="flex items-center justify-between">
                            <div>
                                <div class="font-medium">${f.display_name}</div>
                                <div class="text-xs text-slate-400">${f.description || ''}</div>
                            </div>
                            <div class="toggle ${f.default_enabled ? 'on' : 'off'}" onclick="toggleFeature('${f.feature_key}', ${!f.default_enabled})">
                                <span class="toggle-knob"></span>
                            </div>
                        </div>
                    `).join('')}
                </div>
            </div>
        `).join('');

        document.getElementById('features-list').innerHTML = html;

    } catch (e) {
        showToast('Failed to load features: ' + e.message, 'error');
    }
}

async function toggleFeature(featureKey, enabled) {
    try {
        await api(`/features/${featureKey}`, {
            method: 'PATCH',
            body: JSON.stringify({ enabled })
        });
        showToast(`Feature ${enabled ? 'enabled' : 'disabled'}`, 'success');
        loadFeatures();
    } catch (e) {
        showToast('Failed to update feature: ' + e.message, 'error');
    }
}

// Themes
async function loadThemes() {
    try {
        const data = await api('/themes?include_disabled=true');

        const html = data.themes.map(theme => `
            <div class="bg-surface-dark rounded-xl border border-slate-700/50 overflow-hidden">
                <div class="h-24 p-4" style="background-color: ${theme.css_variables['--bg-primary'] || '#0f172a'}">
                    <div class="flex gap-2">
                        <div class="w-8 h-8 rounded" style="background-color: ${theme.css_variables['--bg-surface'] || '#1e293b'}"></div>
                        <div class="w-8 h-8 rounded" style="background-color: ${theme.css_variables['--accent'] || '#144bb8'}"></div>
                    </div>
                </div>
                <div class="p-4">
                    <div class="flex items-center justify-between mb-2">
                        <h3 class="font-medium">${escapeHtml(theme.display_name)}</h3>
                        ${theme.is_system ? '<span class="badge badge-info">System</span>' : ''}
                        ${!theme.is_enabled ? '<span class="badge badge-danger">Disabled</span>' : ''}
                    </div>
                    <p class="text-xs text-slate-400 mb-3">${escapeHtml(theme.description || '')}</p>
                    <div class="flex gap-2">
                        ${!theme.is_system ? `
                            <button onclick="toggleTheme('${theme.name}', ${!theme.is_enabled})" class="btn btn-secondary btn-sm flex-1">
                                ${theme.is_enabled ? 'Disable' : 'Enable'}
                            </button>
                            <button onclick="confirmDeleteTheme('${theme.name}', '${escapeHtml(theme.display_name)}')" class="btn btn-danger btn-sm">
                                <span class="material-symbols-outlined text-sm">delete</span>
                            </button>
                        ` : `
                            <button onclick="toggleTheme('${theme.name}', ${!theme.is_enabled})" class="btn btn-secondary btn-sm flex-1">
                                ${theme.is_enabled ? 'Disable' : 'Enable'}
                            </button>
                        `}
                    </div>
                </div>
            </div>
        `).join('');

        document.getElementById('themes-list').innerHTML = html;

    } catch (e) {
        showToast('Failed to load themes: ' + e.message, 'error');
    }
}

function showCreateThemeModal() {
    document.getElementById('create-theme-form').reset();
    showModal('create-theme-modal');
}

async function createTheme(event) {
    event.preventDefault();
    const form = event.target;

    let cssVariables;
    try {
        cssVariables = JSON.parse(form.css_variables.value);
    } catch (e) {
        showToast('Invalid CSS variables JSON', 'error');
        return;
    }

    const data = {
        name: form.name.value,
        display_name: form.display_name.value,
        description: form.description.value || null,
        css_variables: cssVariables
    };

    try {
        await api('/themes', {
            method: 'POST',
            body: JSON.stringify(data)
        });
        hideModal('create-theme-modal');
        showToast('Theme created successfully', 'success');
        loadThemes();
    } catch (e) {
        showToast('Failed to create theme: ' + e.message, 'error');
    }
}

async function toggleTheme(themeName, enabled) {
    try {
        await api(`/themes/${themeName}`, {
            method: 'PATCH',
            body: JSON.stringify({ is_enabled: enabled })
        });
        showToast(`Theme ${enabled ? 'enabled' : 'disabled'}`, 'success');
        loadThemes();
    } catch (e) {
        showToast('Failed to update theme: ' + e.message, 'error');
    }
}

function confirmDeleteTheme(themeName, displayName) {
    document.getElementById('confirm-delete-message').textContent =
        `Are you sure you want to delete the theme "${displayName}"? This action cannot be undone.`;

    document.getElementById('confirm-delete-btn').onclick = () => deleteTheme(themeName);
    showModal('confirm-delete-modal');
}

async function deleteTheme(themeName) {
    try {
        await api(`/themes/${themeName}`, { method: 'DELETE' });
        hideModal('confirm-delete-modal');
        showToast('Theme deleted successfully', 'success');
        loadThemes();
    } catch (e) {
        showToast('Failed to delete theme: ' + e.message, 'error');
    }
}

// Audit Log
async function loadAuditLog() {
    try {
        const data = await api(`/audit-log?page=${auditPage}&page_size=50`);

        const tbody = document.getElementById('audit-table-body');
        tbody.innerHTML = data.entries.map(e => `
            <tr>
                <td class="text-slate-400 text-xs">${formatDate(e.created_at)}</td>
                <td>${escapeHtml(e.admin_username || 'Unknown')}</td>
                <td><span class="badge badge-info">${formatAction(e.action)}</span></td>
                <td>${e.target_type}: ${e.target_id || '-'}</td>
                <td class="text-xs text-slate-400 max-w-xs truncate">${formatDetails(e.details)}</td>
                <td class="text-xs text-slate-500">${e.ip_address || '-'}</td>
            </tr>
        `).join('');

        // Pagination
        const pagination = document.getElementById('audit-pagination');
        pagination.innerHTML = `
            <span class="text-sm text-slate-400">
                Page ${auditPage} of ${data.total_pages}
            </span>
            <div class="flex gap-2">
                <button onclick="auditPage--; loadAuditLog()" class="btn btn-secondary btn-sm" ${auditPage === 1 ? 'disabled' : ''}>
                    Previous
                </button>
                <button onclick="auditPage++; loadAuditLog()" class="btn btn-secondary btn-sm" ${auditPage >= data.total_pages ? 'disabled' : ''}>
                    Next
                </button>
            </div>
        `;

    } catch (e) {
        showToast('Failed to load audit log: ' + e.message, 'error');
    }
}

// Utility functions
function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function formatDate(dateStr) {
    if (!dateStr) return '-';
    const date = new Date(dateStr);
    return date.toLocaleString();
}

function formatAction(action) {
    return action.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
}

function formatDetails(details) {
    if (!details) return '-';
    if (typeof details === 'object') {
        return JSON.stringify(details);
    }
    return String(details);
}

// Initialize on load
document.addEventListener('DOMContentLoaded', init);
