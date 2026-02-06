/**
 * Authentication Manager for BrinChat
 * Handles user login, registration, and session management
 */
class AuthManager {
    constructor() {
        this.user = null;
        this.token = null;
        this.onAuthChange = null;
        this.refreshInterval = null;
        this.SESSION_MARKER_KEY = 'brinchat_session_active';
    }

    /**
     * Initialize authentication state
     * Returns: { authenticated: boolean, isNewSession: boolean }
     */
    async init() {
        // Check if user has logged in before (localStorage persists across tab kills on mobile)
        const hasSessionMarker = localStorage.getItem(this.SESSION_MARKER_KEY) === 'true';

        // Try to get current user from existing session cookie
        try {
            const response = await fetch('/api/auth/me', {
                credentials: 'include'
            });

            if (response.ok) {
                // Cookie is valid, but check if this is a new tab
                if (!hasSessionMarker) {
                    // New tab detected - require re-login
                    console.debug('New tab detected, requiring re-authentication');
                    return { authenticated: false, isNewSession: true };
                }

                // Both cookie and session marker valid - continue
                this.user = await response.json();
                this.startTokenRefresh();
                this.notifyAuthChange();
                return { authenticated: true, isNewSession: false };
            }
        } catch (error) {
            console.debug('No existing session');
        }

        return { authenticated: false, isNewSession: !hasSessionMarker };
    }

    /**
     * Mark the session as active (called after successful login)
     */
    markSessionActive() {
        localStorage.setItem(this.SESSION_MARKER_KEY, 'true');
    }

    /**
     * Clear session marker (called on logout)
     */
    clearSessionMarker() {
        localStorage.removeItem(this.SESSION_MARKER_KEY);
    }

    startTokenRefresh() {
        this.stopTokenRefresh();
        // Refresh every 20 minutes (token expires in 24 hours)
        this.refreshInterval = setInterval(() => this.refreshToken(), 20 * 60 * 1000);
    }

    stopTokenRefresh() {
        if (this.refreshInterval) {
            clearInterval(this.refreshInterval);
            this.refreshInterval = null;
        }
    }

    async refreshToken() {
        try {
            const response = await fetch('/api/auth/refresh', {
                method: 'POST',
                credentials: 'include'
            });
            if (!response.ok) {
                console.warn('Token refresh failed, logging out');
                this.stopTokenRefresh();
                await this.logout();
            }
        } catch (error) {
            console.error('Token refresh error:', error);
        }
    }

    /**
     * Register a new user
     */
    async register(username, password, email = null) {
        const response = await fetch('/api/auth/register', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            credentials: 'include',
            body: JSON.stringify({
                username,
                password,
                email: email || null
            })
        });

        if (!response.ok) {
            const error = await response.json();
            // Handle FastAPI validation errors (array of objects)
            if (Array.isArray(error.detail)) {
                const messages = error.detail.map(e => e.msg || e.message || JSON.stringify(e));
                throw new Error(messages.join(', '));
            }
            throw new Error(error.detail || 'Registration failed');
        }

        const data = await response.json();
        this.user = data.user;
        this.token = data.access_token;
        this.markSessionActive();
        this.startTokenRefresh();
        this.notifyAuthChange();
        return data;
    }

    /**
     * Login with username and password
     */
    async login(username, password) {
        const response = await fetch('/api/auth/login', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            credentials: 'include',
            body: JSON.stringify({ username, password })
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Login failed');
        }

        const data = await response.json();
        this.user = data.user;
        this.token = data.access_token;
        this.markSessionActive();
        this.startTokenRefresh();
        this.notifyAuthChange();
        return data;
    }

    /**
     * Logout current user
     */
    async logout() {
        this.stopTokenRefresh();
        this.clearSessionMarker();
        try {
            await fetch('/api/auth/logout', {
                method: 'POST',
                credentials: 'include'
            });
        } catch (error) {
            console.error('Logout error:', error);
        }

        this.user = null;
        this.token = null;
        this.notifyAuthChange();
    }

    /**
     * Check if user is authenticated
     */
    isAuthenticated() {
        return this.user !== null;
    }

    /**
     * Get current user
     */
    getUser() {
        return this.user;
    }

    /**
     * Change password
     */
    async changePassword(currentPassword, newPassword) {
        const response = await fetch('/api/auth/change-password', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json'
            },
            credentials: 'include',
            body: JSON.stringify({
                current_password: currentPassword,
                new_password: newPassword
            })
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Password change failed');
        }

        return await response.json();
    }

    /**
     * Delete account
     */
    async deleteAccount() {
        this.stopTokenRefresh();
        const response = await fetch('/api/auth/account', {
            method: 'DELETE',
            credentials: 'include'
        });

        if (!response.ok) {
            const error = await response.json();
            throw new Error(error.detail || 'Account deletion failed');
        }

        this.user = null;
        this.token = null;
        this.notifyAuthChange();
        return true;
    }

    /**
     * Notify listeners of auth state change
     */
    notifyAuthChange() {
        if (this.onAuthChange) {
            this.onAuthChange(this.user);
        }
    }

    /**
     * Set callback for auth state changes
     */
    setOnAuthChange(callback) {
        this.onAuthChange = callback;
    }
}

// Global auth manager instance
const authManager = new AuthManager();
