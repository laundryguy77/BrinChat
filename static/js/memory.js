/**
 * Memory Manager - Handles memory display and management in settings
 */
class MemoryManager {
    constructor() {
        this.memories = [];
        this.stats = { total: 0, by_category: {} };
    }

    init() {
        this.loadMemories();
    }

    async loadMemories() {
        try {
            const response = await fetch('/api/memory', { credentials: 'include' });
            if (!response.ok) throw new Error('Failed to load memories');

            const data = await response.json();
            this.memories = data.memories || [];
            this.stats = data.stats || { total: 0, by_category: {} };
            this.render();
        } catch (error) {
            console.error('Failed to load memories:', error);
            this.memories = [];
            this.stats = { total: 0, by_category: {} };
            this.render();
        }
    }

    async deleteMemory(memoryId) {
        if (!confirm('Delete this memory?')) return;

        try {
            const response = await fetch(`/api/memory/${memoryId}`, {
                method: 'DELETE',
                credentials: 'include'
            });

            if (!response.ok) throw new Error('Failed to delete');
            await this.loadMemories();
        } catch (error) {
            console.error('Failed to delete memory:', error);
            alert('Failed to delete memory');
        }
    }

    async clearAllMemories() {
        if (!confirm('Clear ALL memories? This cannot be undone.')) return;

        try {
            const response = await fetch('/api/memory', {
                method: 'DELETE',
                credentials: 'include'
            });

            if (!response.ok) throw new Error('Failed to clear');
            await this.loadMemories();
        } catch (error) {
            console.error('Failed to clear memories:', error);
            alert('Failed to clear memories');
        }
    }

    render() {
        // Update stats
        const countEl = document.getElementById('memory-count');
        const categoriesEl = document.getElementById('memory-categories');

        if (countEl) countEl.textContent = this.stats.total;
        if (categoriesEl) {
            categoriesEl.textContent = Object.keys(this.stats.by_category).length;
        }

        // Render memory list
        const listEl = document.getElementById('memory-list');
        if (!listEl) return;

        if (this.memories.length === 0) {
            listEl.innerHTML = `
                <p class="text-gray-500 text-sm text-center py-4">
                    No memories yet. The AI will learn about you over time.
                </p>
            `;
            return;
        }

        // Group by category
        const byCategory = {};
        this.memories.forEach(mem => {
            const cat = mem.category || 'general';
            if (!byCategory[cat]) byCategory[cat] = [];
            byCategory[cat].push(mem);
        });

        const categoryLabels = {
            personal: 'Personal Information',
            preference: 'Preferences',
            topic: 'Topics & Projects',
            instruction: 'Instructions',
            general: 'General'
        };

        const categoryIcons = {
            personal: 'person',
            preference: 'favorite',
            topic: 'topic',
            instruction: 'rule',
            general: 'memory'
        };

        let html = '';
        for (const [cat, mems] of Object.entries(byCategory)) {
            const label = categoryLabels[cat] || cat;
            const icon = categoryIcons[cat] || 'memory';

            html += `
                <div class="mb-4">
                    <div class="flex items-center gap-2 text-sm font-medium text-gray-400 mb-2">
                        <span class="material-symbols-outlined text-primary text-lg">${icon}</span>
                        ${label} (${mems.length})
                    </div>
                    <div class="space-y-1">
            `;

            for (const mem of mems) {
                const date = new Date(mem.created_at).toLocaleDateString();
                html += `
                    <div class="flex items-start gap-2 p-2 bg-background-dark/50 rounded-lg group">
                        <div class="flex-1 min-w-0">
                            <p class="text-sm text-gray-300 break-words">${this.escapeHtml(mem.content)}</p>
                            <p class="text-xs text-gray-500 mt-1">
                                ${mem.source === 'explicit' ? 'You asked' : 'Learned'} &bull; ${date}
                                ${mem.importance >= 8 ? ' &bull; <span class="text-yellow-500">Important</span>' : ''}
                            </p>
                        </div>
                        <button
                            onclick="memoryManager.deleteMemory('${mem.id}')"
                            class="p-1 text-gray-500 hover:text-red-400 opacity-0 group-hover:opacity-100 transition-all"
                            title="Delete memory"
                        >
                            <span class="material-symbols-outlined text-sm">delete</span>
                        </button>
                    </div>
                `;
            }

            html += '</div></div>';
        }

        listEl.innerHTML = html;
    }

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
}

// Global memory manager instance
const memoryManager = new MemoryManager();
