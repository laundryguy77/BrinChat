/**
 * MCP Server Manager for BrinChat
 * Handles MCP server configuration, connection, and management
 */
class MCPManager {
    constructor() {
        this.servers = [];
    }

    /**
     * Initialize MCP UI and event listeners
     */
    init() {
        this.loadServers();
    }

    /**
     * Load MCP server list from API
     */
    async loadServers() {
        try {
            const response = await fetch('/api/mcp/servers', {
                credentials: 'include'
            });

            if (response.ok) {
                this.servers = await response.json();
                this.render();
            }
        } catch (error) {
            console.error('Failed to load MCP servers:', error);
        }
    }

    /**
     * Render the MCP servers list
     */
    render() {
        const container = document.getElementById('mcp-servers');
        if (!container) return;

        if (this.servers.length === 0) {
            container.innerHTML = `
                <div class="text-center text-gray-500 text-sm py-4">
                    No MCP servers configured
                </div>
            `;
            return;
        }

        container.innerHTML = this.servers.map(server => `
            <div class="flex items-center gap-3 p-3 bg-background-dark rounded-lg group mb-2" data-id="${server.id}">
                <div class="size-2 rounded-full ${server.connected ? 'bg-green-500 shadow-[0_0_6px_rgba(34,197,94,0.5)]' : 'bg-gray-500'}" title="${server.connected ? 'Connected' : 'Disconnected'}"></div>
                <div class="flex-1 min-w-0">
                    <div class="text-sm text-white truncate" title="${server.name}">${server.name}</div>
                    <div class="text-xs text-gray-500 truncate">${server.command || server.url || 'No command'}</div>
                </div>
                <button class="mcp-toggle-btn p-1.5 rounded-lg ${server.connected ? 'text-red-400 hover:bg-red-400/10' : 'text-green-400 hover:bg-green-400/10'} transition-colors" data-id="${server.id}" data-connected="${server.connected}" title="${server.connected ? 'Disconnect' : 'Connect'}">
                    <span class="material-symbols-outlined text-sm">${server.connected ? 'stop' : 'play_arrow'}</span>
                </button>
                <button class="mcp-delete-btn p-1.5 rounded-lg text-gray-500 hover:text-red-400 hover:bg-red-400/10 transition-colors opacity-0 group-hover:opacity-100" data-id="${server.id}" title="Delete">
                    <span class="material-symbols-outlined text-sm">delete</span>
                </button>
            </div>
        `).join('');

        // Add event listeners
        container.querySelectorAll('.mcp-toggle-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                const isConnected = btn.dataset.connected === 'true';
                this.toggleConnection(btn.dataset.id, isConnected);
            });
        });

        container.querySelectorAll('.mcp-delete-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                this.deleteServer(btn.dataset.id);
            });
        });
    }

    /**
     * Show the add server modal
     */
    showAddModal() {
        // Create modal if it doesn't exist
        let modal = document.getElementById('mcp-add-modal');
        if (!modal) {
            modal = document.createElement('div');
            modal.id = 'mcp-add-modal';
            modal.className = 'fixed inset-0 bg-black/70 backdrop-blur-sm z-[60] flex items-center justify-center p-4';
            modal.innerHTML = `
                <div class="bg-surface-dark border border-gray-700 rounded-2xl w-full max-w-md shadow-2xl">
                    <div class="flex items-center justify-between p-6 border-b border-gray-700">
                        <h2 class="font-display font-bold text-xl text-white">Add MCP Server</h2>
                        <button id="mcp-modal-close" class="text-gray-400 hover:text-white transition-colors">
                            <span class="material-symbols-outlined">close</span>
                        </button>
                    </div>
                    <div class="p-6 space-y-4">
                        <div>
                            <label class="block text-sm font-medium text-gray-300 mb-2">Server Name</label>
                            <input type="text" id="mcp-server-name" class="w-full bg-background-dark border border-gray-700 rounded-xl p-3 text-white placeholder-gray-500 text-sm focus:ring-2 focus:ring-primary/50 focus:border-primary/50 transition-all" placeholder="e.g., Filesystem, Database">
                        </div>
                        <div>
                            <label class="block text-sm font-medium text-gray-300 mb-2">Command</label>
                            <input type="text" id="mcp-server-command" class="w-full bg-background-dark border border-gray-700 rounded-xl p-3 text-white placeholder-gray-500 text-sm focus:ring-2 focus:ring-primary/50 focus:border-primary/50 transition-all" placeholder="e.g., npx, python, node">
                        </div>
                        <div>
                            <label class="block text-sm font-medium text-gray-300 mb-2">Arguments (space-separated)</label>
                            <input type="text" id="mcp-server-args" class="w-full bg-background-dark border border-gray-700 rounded-xl p-3 text-white placeholder-gray-500 text-sm focus:ring-2 focus:ring-primary/50 focus:border-primary/50 transition-all" placeholder="e.g., -y @modelcontextprotocol/server-filesystem /home/user">
                        </div>
                        <p class="text-xs text-gray-500">MCP servers extend the AI's capabilities with additional tools. The command will be run as a subprocess.</p>
                    </div>
                    <div class="flex gap-3 p-6 pt-0">
                        <button id="mcp-modal-cancel" class="flex-1 py-2.5 bg-gray-700 hover:bg-gray-600 text-white rounded-xl font-medium transition-colors">Cancel</button>
                        <button id="mcp-modal-save" class="flex-1 py-2.5 bg-primary hover:bg-primary-hover text-white rounded-xl font-medium transition-all shadow-lg shadow-primary/20">Add Server</button>
                    </div>
                </div>
            `;
            document.body.appendChild(modal);

            // Event listeners
            document.getElementById('mcp-modal-close').addEventListener('click', () => this.hideAddModal());
            document.getElementById('mcp-modal-cancel').addEventListener('click', () => this.hideAddModal());
            document.getElementById('mcp-modal-save').addEventListener('click', () => this.addServer());
            modal.addEventListener('click', (e) => {
                if (e.target === modal) this.hideAddModal();
            });

            // ESC key to close
            document.addEventListener('keydown', (e) => {
                if (e.key === 'Escape') {
                    const mcpModal = document.getElementById('mcp-add-modal');
                    if (mcpModal && !mcpModal.classList.contains('hidden')) {
                        this.hideAddModal();
                    }
                }
            });
        }

        // Clear form and show
        document.getElementById('mcp-server-name').value = '';
        document.getElementById('mcp-server-command').value = '';
        document.getElementById('mcp-server-args').value = '';
        modal.classList.remove('hidden');
    }

    /**
     * Hide the add server modal
     */
    hideAddModal() {
        const modal = document.getElementById('mcp-add-modal');
        if (modal) modal.classList.add('hidden');
    }

    /**
     * Add a new MCP server
     */
    async addServer() {
        const name = document.getElementById('mcp-server-name').value.trim();
        const command = document.getElementById('mcp-server-command').value.trim();
        const argsStr = document.getElementById('mcp-server-args').value.trim();

        if (!name) {
            alert('Server name is required');
            return;
        }

        if (!command) {
            alert('Command is required');
            return;
        }

        // Parse args - handle quoted strings
        const args = argsStr ? this.parseArgs(argsStr) : [];

        try {
            const response = await fetch('/api/mcp/servers', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                credentials: 'include',
                body: JSON.stringify({
                    name,
                    transport: 'stdio',
                    command,
                    args
                })
            });

            if (response.ok) {
                this.hideAddModal();
                await this.loadServers();
            } else {
                const error = await response.json();
                alert(`Failed to add server: ${error.detail || 'Unknown error'}`);
            }
        } catch (error) {
            console.error('Add server error:', error);
            alert('Failed to add MCP server');
        }
    }

    /**
     * Parse command arguments, respecting quotes
     */
    parseArgs(argsStr) {
        const args = [];
        let current = '';
        let inQuote = false;
        let quoteChar = '';

        for (const char of argsStr) {
            if ((char === '"' || char === "'") && !inQuote) {
                inQuote = true;
                quoteChar = char;
            } else if (char === quoteChar && inQuote) {
                inQuote = false;
                quoteChar = '';
            } else if (char === ' ' && !inQuote) {
                if (current) {
                    args.push(current);
                    current = '';
                }
            } else {
                current += char;
            }
        }

        if (current) {
            args.push(current);
        }

        return args;
    }

    /**
     * Delete an MCP server
     */
    async deleteServer(serverId) {
        if (!confirm('Delete this MCP server configuration?')) return;

        try {
            const response = await fetch(`/api/mcp/servers/${serverId}`, {
                method: 'DELETE',
                credentials: 'include'
            });

            if (response.ok) {
                await this.loadServers();
            } else {
                alert('Failed to delete server');
            }
        } catch (error) {
            console.error('Delete server error:', error);
            alert('Failed to delete MCP server');
        }
    }

    /**
     * Toggle server connection
     */
    async toggleConnection(serverId, isConnected) {
        const endpoint = isConnected ? 'disconnect' : 'connect';

        try {
            const response = await fetch(`/api/mcp/servers/${serverId}/${endpoint}`, {
                method: 'POST',
                credentials: 'include'
            });

            if (response.ok) {
                const result = await response.json();
                if (!isConnected && result.tools) {
                    console.log(`Connected to MCP server, got ${result.tools.length} tools:`, result.tools);
                }
                await this.loadServers();
            } else {
                const error = await response.json();
                alert(`Failed to ${endpoint}: ${error.detail || 'Unknown error'}`);
            }
        } catch (error) {
            console.error(`${endpoint} error:`, error);
            alert(`Failed to ${endpoint} MCP server`);
        }
    }
}

// Global MCP manager instance
const mcpManager = new MCPManager();
