// Q-aura Front-end App logic (Client router & API client)
const API_URL = window.location.port === "3000" ? "http://localhost:8000" : window.location.origin;
let tempContacts = [];

const state = {
    token: localStorage.getItem("qa_token") || null,
    user: JSON.parse(localStorage.getItem("qa_user")) || null,
    tenant_id: localStorage.getItem("qa_tenant_id") || null,
    conversations: [],
    activeConversationId: null,
    ws: null,
    conversationsCache: {},
    messagesCache: {}
};

// --- Web Audio API Synth Sound & Desktop Notifications ---
let audioCtx = null;

function playNewMessageSound() {
    try {
        if (!audioCtx) {
            audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        }
        if (audioCtx.state === "suspended") {
            audioCtx.resume();
        }
        
        const now = audioCtx.currentTime;
        const osc = audioCtx.createOscillator();
        const gain = audioCtx.createGain();
        
        osc.type = "sine";
        // Chime duplo agradável: 587.33Hz (D5) -> 880Hz (A5)
        osc.frequency.setValueAtTime(587.33, now);
        osc.frequency.exponentialRampToValueAtTime(880.00, now + 0.08);
        
        gain.gain.setValueAtTime(0, now);
        gain.gain.linearRampToValueAtTime(0.25, now + 0.015);
        gain.gain.exponentialRampToValueAtTime(0.001, now + 0.35);
        
        osc.connect(gain);
        gain.connect(audioCtx.destination);
        
        osc.start(now);
        osc.stop(now + 0.35);
    } catch (e) {
        console.warn("[Sound Notice] Audio playback unavailable:", e);
    }
}

function requestNotificationPermission() {
    if ("Notification" in window && Notification.permission === "default") {
        Notification.requestPermission();
    }
}

function showDesktopNotification(title, body, icon) {
    if ("Notification" in window && Notification.permission === "granted") {
        try {
            new Notification(title, {
                body: body || "Nova mensagem recebida",
                icon: icon || "/favicon.png"
            });
        } catch (e) {
            console.warn("[Notification Notice] Could not show desktop notification:", e);
        }
    }
}

// Solocita permissão de notificação no primeiro clique da página
document.addEventListener("click", () => {
    requestNotificationPermission();
}, { once: true });

// --- Toast Notification Helper ---
function showToast(message, type = "success") {
    const container = document.getElementById("toast-container");
    if (!container) return;
    
    const toast = document.createElement("div");
    toast.className = `toast ${type}`;
    
    const icon = document.createElement("i");
    if (type === "success") {
        icon.className = "fa-solid fa-circle-check";
    } else {
        icon.className = "fa-solid fa-circle-exclamation";
    }
    
    const text = document.createElement("span");
    text.innerText = message;
    
    toast.appendChild(icon);
    toast.appendChild(text);
    container.appendChild(toast);
    
    setTimeout(() => {
        toast.classList.add("fade-out");
        setTimeout(() => {
            toast.remove();
        }, 300);
    }, 4000);
}

// --- HTML Escape Helper ---
function escapeHTML(str) {
    if (str === null || str === undefined) return "";
    return String(str)
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
}
window.escapeHTML = escapeHTML;

// --- WhatsApp Formatting Helper ---
function formatMessageBody(body) {
    if (!body) return "";
    if (body === "[Unsupported]" || body === "[unsupported]") {
        return `<span style="opacity: 0.85; font-style: italic; display: inline-flex; align-items: center; gap: 4px;"><i class="fa-solid fa-circle-info" style="color: var(--color-primary);"></i> [Mensagem de Sistema / Autenticação do WhatsApp]</span>`;
    }
    // Safe escape HTML to prevent XSS
    let escaped = escapeHTML(body);
        
    // Replace *bold* with <strong>bold</strong>
    escaped = escaped.replace(/\*(.*?)\*/g, "<strong>$1</strong>");
    // Replace _italic_ with <em>italic</em>
    escaped = escaped.replace(/_(.*?)_/g, "<em>$1</em>");
    // Replace \n with <br>
    escaped = escaped.replace(/\n/g, "<br>");
    
    return escaped;
}

// --- Relative Time Helper ---
function formatRelativeTime(isoStr) {
    if (!isoStr) return "";
    let cleanStr = String(isoStr);
    if (!cleanStr.endsWith("Z") && !cleanStr.includes("+") && !cleanStr.includes("-")) {
        cleanStr += "Z";
    }
    const d = new Date(cleanStr);
    const now = new Date();
    const diffMs = now - d;
    const diffMin = Math.floor(diffMs / 60000);
    const diffH = Math.floor(diffMs / 3600000);
    const diffD = Math.floor(diffMs / 86400000);
    if (diffMin < 1) return "agora";
    if (diffMin < 60) return `${diffMin}m`;
    if (diffH < 24) return `${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`;
    if (diffD === 1) return "ontem";
    return `${String(d.getDate()).padStart(2,'0')}/${String(d.getMonth()+1).padStart(2,'0')}`;
}

// --- Render Message Bubble Helper ---
function renderMessageBubble(m) {
    // Renderização de Notas Internas Privadas (Anotações da Equipe)
    if (m.internal_note && m.sender_type !== "system" && m.message_type !== "system") {
        const noteCard = document.createElement("div");
        noteCard.className = "message-bubble outgoing internal-note";
        if (m.id) noteCard.setAttribute("data-msg-id", m.id);
        
        let cleanStr = m.created_at ? String(m.created_at) : "";
        if (cleanStr && !cleanStr.endsWith("Z") && !cleanStr.includes("+") && !cleanStr.includes("-")) {
            cleanStr += "Z";
        }
        const d = cleanStr ? new Date(cleanStr) : null;
        const timeStr = d ? `${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}` : "";
        
        noteCard.style.cssText = "background: rgba(245, 158, 11, 0.15) !important; border: 1px solid rgba(245, 158, 11, 0.4) !important; border-left: 4px solid #f59e0b !important; color: #fef08a !important; margin-left: auto; max-width: 80%; border-radius: 8px; padding: 10px 14px; margin-bottom: 8px;";
        
        noteCard.innerHTML = `
            <div style="display: flex; align-items: center; justify-content: space-between; margin-bottom: 4px; font-size: 11px; font-weight: 700; color: #fbbf24;">
                <span><i class="fa-solid fa-lock" style="margin-right: 4px;"></i> Nota Interna Privada</span>
                <span style="opacity: 0.8; font-size: 10px;">${timeStr}</span>
            </div>
            <div style="font-size: 13px; color: #fff; line-height: 1.4; white-space: pre-wrap;">${escapeHTML(m.body || "")}</div>
        `;
        return noteCard;
    }

    // Renderização de Eventos do Sistema
    if (m.sender_type === "system" || m.message_type === "system") {
        const sysEvent = document.createElement("div");
        sysEvent.className = "chat-system-event";
        if (m.id) sysEvent.setAttribute("data-msg-id", m.id);
        
        let cleanStr = m.created_at ? String(m.created_at) : "";
        if (cleanStr && !cleanStr.endsWith("Z") && !cleanStr.includes("+") && !cleanStr.includes("-")) {
            cleanStr += "Z";
        }
        const d = cleanStr ? new Date(cleanStr) : null;
        const timeStr = d ? `${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}` : "";
        
        sysEvent.innerHTML = `
            <div class="system-event-pill">
                <span>${m.body}</span>
                ${timeStr ? `<small style="margin-left: 6px; opacity: 0.65; font-size: 10px;">${timeStr}</small>` : ''}
            </div>
        `;
        return sysEvent;
    }

    const bubble = document.createElement("div");
    bubble.className = `message-bubble ${m.sender_type === 'contact' ? 'incoming' : 'outgoing'}`;
    // Tag para deduplicação
    if (m.id) bubble.setAttribute("data-msg-id", m.id);
    if (m.meta_message_id) bubble.setAttribute("data-meta-id", m.meta_message_id);

    // Indicador de remetente para bot/agente
    if (m.sender_type === 'bot') {
        const senderLabel = document.createElement("div");
        senderLabel.style.cssText = "font-size:10px;opacity:0.6;margin-bottom:3px;font-weight:600;";
        senderLabel.innerText = "🤖 Bot";
        bubble.appendChild(senderLabel);
    }

    // Helper to get media source URL
    const getMediaSrc = (mediaUrl) => {
        if (!mediaUrl) return "";
        if (mediaUrl.startsWith("http://") || mediaUrl.startsWith("https://") || mediaUrl.startsWith("blob:")) {
            return mediaUrl;
        }
        if (mediaUrl.startsWith("/")) {
            return `${API_URL}${mediaUrl}`;
        }
        return `${API_URL}/api/inbox/media/${mediaUrl}?token=${state.token}`;
    };

    if ((m.message_type === "image" || m.message_type === "sticker") && m.media_url) {
        const img = document.createElement("img");
        const imgSrc = getMediaSrc(m.media_url);
        img.src = imgSrc;
        img.alt = m.message_type === "sticker" ? "Figurinha" : "Imagem";
        img.className = "chat-media-image";
        if (m.message_type === "sticker") {
            img.style.maxWidth = "130px";
            img.style.maxHeight = "130px";
            img.style.objectFit = "contain";
            img.style.background = "transparent";
        }
        img.onclick = () => window.open(imgSrc, "_blank");
        bubble.appendChild(img);
        
        if (m.body && m.body !== "[Imagem]" && m.body !== "[Image]") {
            const caption = document.createElement("div");
            caption.innerHTML = formatMessageBody(m.body);
            caption.style.marginTop = "8px";
            bubble.appendChild(caption);
        }
    } else if ((m.message_type === "audio" || m.message_type === "voice") && m.media_url) {
        const audio = document.createElement("audio");
        audio.src = getMediaSrc(m.media_url);
        audio.controls = true;
        audio.style.maxWidth = "100%";
        audio.style.display = "block";
        audio.style.marginTop = "4px";
        bubble.appendChild(audio);
        
        if (m.body && m.body !== "[Áudio]" && m.body !== "[Audio]") {
            const caption = document.createElement("div");
            caption.innerHTML = formatMessageBody(m.body);
            caption.style.marginTop = "8px";
            bubble.appendChild(caption);
        }
    } else if (m.message_type === "video" && m.media_url) {
        const video = document.createElement("video");
        video.src = getMediaSrc(m.media_url);
        video.controls = true;
        video.style.maxWidth = "280px";
        video.style.borderRadius = "8px";
        video.style.display = "block";
        video.style.marginTop = "4px";
        bubble.appendChild(video);
        
        if (m.body && m.body !== "[Vídeo]" && m.body !== "[Video]") {
            const caption = document.createElement("div");
            caption.innerHTML = formatMessageBody(m.body);
            caption.style.marginTop = "8px";
            bubble.appendChild(caption);
        }
    } else if (m.message_type === "document" && m.media_url) {
        const docDiv = document.createElement("div");
        docDiv.className = "document-bubble";
        docDiv.style.display = "flex";
        docDiv.style.alignItems = "center";
        docDiv.style.gap = "8px";
        docDiv.style.padding = "8px";
        docDiv.style.background = "var(--bg-tertiary)";
        docDiv.style.borderRadius = "8px";
        docDiv.style.border = "1px solid var(--border-color)";
        docDiv.style.marginTop = "4px";
        
        const docSrc = getMediaSrc(m.media_url);
        docDiv.innerHTML = `
            <i class="fa-solid fa-file-pdf" style="font-size: 24px; color: #ef4444;"></i>
            <div style="flex: 1; text-align: left; min-width: 0;">
                <div style="font-size: 13px; font-weight: 600; text-overflow: ellipsis; overflow: hidden; white-space: nowrap; color: var(--text-primary);">${m.body || 'Documento'}</div>
                <a href="${docSrc}" target="_blank" style="font-size: 11px; color: var(--color-brand); text-decoration: underline;">Visualizar Documento</a>
            </div>
        `;
        bubble.appendChild(docDiv);
    } else {
        bubble.innerHTML = formatMessageBody(m.body);
    }

    // Timestamp na bolha (com tratamento UTC/local)
    if (m.created_at) {
        const ts = document.createElement("div");
        let cleanStr = String(m.created_at);
        if (!cleanStr.endsWith("Z") && !cleanStr.includes("+") && !cleanStr.includes("-")) {
            cleanStr += "Z";
        }
        const d = new Date(cleanStr);
        ts.style.cssText = "font-size:10px;opacity:0.5;margin-top:4px;text-align:right;";
        ts.innerText = `${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}`;
        bubble.appendChild(ts);
    }
    
    return bubble;
}

// --- WebSocket Status Badge Helper ---
function updateWsStatusBadge(status) {
    const badge = document.getElementById("ws-status-badge");
    const text = document.getElementById("ws-status-text");
    if (!badge || !text) return;

    if (status === "connected") {
        badge.style.background = "rgba(34, 197, 94, 0.15)";
        badge.style.color = "#22c55e";
        badge.style.borderColor = "rgba(34, 197, 94, 0.3)";
        const icon = badge.querySelector("i");
        if (icon) icon.className = "fa-solid fa-circle text-xs";
        text.innerText = "Conectado ao vivo";
    } else if (status === "connecting") {
        badge.style.background = "rgba(234, 179, 8, 0.15)";
        badge.style.color = "#eab308";
        badge.style.borderColor = "rgba(234, 179, 8, 0.3)";
        const icon = badge.querySelector("i");
        if (icon) icon.className = "fa-solid fa-spinner fa-spin text-xs";
        text.innerText = "Reconectando...";
    } else {
        badge.style.background = "rgba(239, 68, 68, 0.15)";
        badge.style.color = "#ef4444";
        badge.style.borderColor = "rgba(239, 68, 68, 0.3)";
        const icon = badge.querySelector("i");
        if (icon) icon.className = "fa-solid fa-circle-xmark text-xs";
        text.innerText = "Desconectado";
    }
}

// --- API Client ---
const api = {
    handleUnauthorized() {
        if (state.isDeauthenticating) return;
        state.isDeauthenticating = true;
        console.warn("[Auth] Sessão expirada (401). Movendo para a tela de login...");
        if (typeof appRouter !== "undefined" && appRouter.logout) {
            appRouter.logout(true);
        } else {
            localStorage.clear();
            window.location.reload();
        }
        setTimeout(() => { state.isDeauthenticating = false; }, 3000);
    },

    async fetchWithTimeout(url, options = {}, timeoutMs = 10000) {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), timeoutMs);
        try {
            const response = await fetch(url, {
                ...options,
                signal: controller.signal
            });
            clearTimeout(timer);
            return response;
        } catch (err) {
            clearTimeout(timer);
            if (err.name === "AbortError") {
                throw new Error("Tempo limite de conexão excedido (Timeout). Tente novamente.");
            }
            throw err;
        }
    },

    async post(endpoint, data, useAuth = true) {
        const headers = { "Content-Type": "application/json" };
        if (useAuth && state.token) {
            headers["Authorization"] = `Bearer ${state.token}`;
        }
        const response = await this.fetchWithTimeout(`${API_URL}${endpoint}`, {
            method: "POST",
            headers,
            body: JSON.stringify(data)
        });
        if (response.status === 401 && useAuth) {
            this.handleUnauthorized();
            throw new Error("Sessão expirada. Redirecionando...");
        }
        if (!response.ok) {
            const err = await response.json();
            throw new Error(err.detail || "Erro desconhecido");
        }
        return response.json();
    },

    async postForm(endpoint, formData, useAuth = true, timeoutMs = 60000) {
        const headers = {};
        if (useAuth && state.token) {
            headers["Authorization"] = `Bearer ${state.token}`;
        }
        const response = await this.fetchWithTimeout(`${API_URL}${endpoint}`, {
            method: "POST",
            headers,
            body: formData
        }, timeoutMs);
        if (response.status === 401 && useAuth) {
            this.handleUnauthorized();
            throw new Error("Sessão expirada. Redirecionando...");
        }
        if (!response.ok) {
            const err = await response.json();
            throw new Error(err.detail || "Erro no envio do arquivo");
        }
        return response.json();
    },

    async get(endpoint) {
        const headers = {};
        if (state.token) {
            headers["Authorization"] = `Bearer ${state.token}`;
        }
        const response = await this.fetchWithTimeout(`${API_URL}${endpoint}`, { headers });
        if (response.status === 401) {
            this.handleUnauthorized();
            throw new Error("Sessão expirada. Redirecionando...");
        }
        if (!response.ok) {
            let detail = `Erro ${response.status}: ${response.statusText}`;
            try {
                const errData = await response.json();
                if (errData && errData.detail) detail = errData.detail;
            } catch (e) {}
            throw new Error(detail);
        }
        return response.json();
    },

    async put(endpoint, data, useAuth = true) {
        const headers = { "Content-Type": "application/json" };
        if (useAuth && state.token) {
            headers["Authorization"] = `Bearer ${state.token}`;
        }
        const response = await this.fetchWithTimeout(`${API_URL}${endpoint}`, {
            method: "PUT",
            headers,
            body: JSON.stringify(data)
        });
        if (response.status === 401 && useAuth) {
            this.handleUnauthorized();
            throw new Error("Sessão expirada. Redirecionando...");
        }
        if (!response.ok) {
            const err = await response.json();
            throw new Error(err.detail || "Erro desconhecido");
        }
        return response.json();
    },

    async delete(endpoint) {
        const headers = {};
        if (state.token) {
            headers["Authorization"] = `Bearer ${state.token}`;
        }
        const response = await this.fetchWithTimeout(`${API_URL}${endpoint}`, {
            method: "DELETE",
            headers
        });
        if (response.status === 401) {
            this.handleUnauthorized();
            throw new Error("Sessão expirada. Redirecionando...");
        }
        if (!response.ok) {
            const err = await response.json();
            throw new Error(err.detail || "Erro ao excluir");
        }
        return response.json();
    }
};

// --- Router ---
const appRouter = {
    navigate(viewName) {
        document.querySelectorAll(".view").forEach(v => v.classList.remove("active"));
        const targetView = document.getElementById(`${viewName}-view`);
        if (targetView) targetView.classList.add("active");
    },

    selectTab(event) {
        event.preventDefault();
        document.querySelectorAll(".menu-item").forEach(i => i.classList.remove("active"));
        event.currentTarget.classList.add("active");
        
        const targetView = event.currentTarget.getAttribute("data-target");
        document.querySelectorAll(".workspace-view").forEach(v => v.classList.remove("active"));
        document.getElementById(targetView).classList.add("active");

        if (targetView === "inbox-view") {
            this.loadConversations();
            this.loadQuickMessages();
        } else if (targetView === "dashboard-view") {
            setTimeout(() => this.loadDashboardMetrics(), 60);
        } else if (targetView === "settings-view") {
            this.loadMetaSettings();
            this.loadQuickMessages();
        } else if (targetView === "chatbot-view") {
            this.loadBotConfig();
        } else if (targetView === "team-view") {
            this.loadTeamUsers();
        } else if (targetView === "crm-view") {
            // Limpa o cache temporário ao mudar de aba
            tempContacts = [];
            updateContactsPreview();
            this.loadCampaigns();
        } else if (targetView === "kanban-view") {
            this.loadKanbanBoard();
        }
    },

    async init() {
        // Inicializa o gerenciador de inatividade e segundo plano (5 minutos)
        this.initInactivityAndVisibilityManager();

        if (state.token) {
            try {
                // Busca o perfil real do usuário logado (com timeout de 10s via fetchWithTimeout)
                const userProfile = await api.get("/api/auth/me");
                state.user = userProfile;
                state.tenant_id = userProfile.tenant_id;
                
                localStorage.setItem("qa_user", JSON.stringify(state.user));
                localStorage.setItem("qa_tenant_id", state.tenant_id);
                
                this.showMainLayout();
                this.connectWebSocket();
                this.startBackgroundSync();
                this.updateProfileUI();
                
                // Pré-carrega as configurações sem bloquear a exibição principal da interface
                Promise.allSettled([
                    this.loadMetaSettings(),
                    this.loadQuickMessages(),
                    this.loadTemplates(),
                    this.loadDashboardMetrics()
                ]);
            } catch (e) {
                console.error("[Init Error] Falha ao verificar credenciais:", e);
                // Se for 401 explícito, realiza logout
                if (e.message && e.message.includes("401")) {
                    this.logout(true);
                } else if (state.user && state.tenant_id) {
                    // Se for falha de rede temporária no F5, aproveita o perfil salvo e mostra o layout sem travar a tela
                    console.warn("[Init Recovery] Usando sessão armazenada localmente devido a oscilação de rede.");
                    this.showMainLayout();
                    this.connectWebSocket();
                    this.startBackgroundSync();
                    this.updateProfileUI();
                } else {
                    this.logout(true);
                }
            }
        } else {
            this.navigate("login");
        }
    },

    showMainLayout() {
        document.querySelectorAll(".view").forEach(v => v.classList.remove("active"));
        const loginView = document.getElementById("login-view");
        if (loginView) loginView.style.display = "none";

        const mainLayout = document.getElementById("main-layout");
        if (mainLayout) {
            mainLayout.classList.remove("layout-hidden");
            mainLayout.style.display = "flex";
        }
        
        // Pausa o vídeo 3D da tela de login para economizar 100% de CPU/GPU
        const loginVid = document.querySelector("#login-view video");
        if (loginVid) {
            try { loginVid.pause(); } catch(e) {}
        }

        // Restore sidebar collapsed state
        if (localStorage.getItem("qa_sidebar_collapsed") === "true") {
            const sidebar = document.querySelector(".sidebar");
            const icon = document.getElementById("toggle-sidebar-icon");
            if (sidebar) sidebar.classList.add("collapsed");
            if (icon) icon.className = "fa-solid fa-outdent";
        }
    },

    toggleSidebar() {
        const sidebar = document.querySelector(".sidebar");
        const icon = document.getElementById("toggle-sidebar-icon");
        if (!sidebar) return;

        sidebar.classList.toggle("collapsed");
        const isCollapsed = sidebar.classList.contains("collapsed");

        if (icon) {
            icon.className = isCollapsed ? "fa-solid fa-indent" : "fa-solid fa-bars-staggered";
        }

        localStorage.setItem("qa_sidebar_collapsed", isCollapsed ? "true" : "false");
    },

    updateProfileUI() {
        if (state.user) {
            // Redirect superadmin to their dedicated panel
            if (state.user.role === "superadmin") {
                window.location.href = "superadmin.html";
                return;
            }

            document.getElementById("user-display-name").innerText = state.user.name;
            document.getElementById("user-display-role").innerText = state.user.role.toUpperCase();
            document.getElementById("user-avatar-char").innerText = state.user.name.charAt(0).toUpperCase();

            // Set Meta configurations if they exist
            document.getElementById("webhook-generated-url").innerText = `${API_URL}/api/webhook/${state.tenant_id}`;

            // 1. Role-based visibility (RBAC)
            if (state.user.role === "administrator") {
                document.querySelectorAll(".admin-only").forEach(el => el.style.display = "flex");
                document.querySelectorAll(".admin-manager-only").forEach(el => el.style.display = "flex");
            } else if (state.user.role === "manager") {
                document.querySelectorAll(".admin-only").forEach(el => el.style.display = "none");
                document.querySelectorAll(".admin-manager-only").forEach(el => el.style.display = "flex");
            } else {
                document.querySelectorAll(".admin-only").forEach(el => el.style.display = "none");
                document.querySelectorAll(".admin-manager-only").forEach(el => el.style.display = "none");
            }

            // 2. Module-based visibility (SaaS Plans)
            const enabledModules = state.user.enabled_modules || [];
            document.querySelectorAll(".sidebar-menu .menu-item[data-module]").forEach(el => {
                const moduleName = el.getAttribute("data-module");
                // If module is not enabled for this company, hide the link
                if (!enabledModules.includes(moduleName)) {
                    el.style.display = "none";
                }
            });

            // Quick message global option visibility
            const globalLabel = document.getElementById("quick-global-label");
            if (globalLabel) {
                if (state.user.role === "administrator" || state.user.role === "manager") {
                    globalLabel.style.display = "flex";
                } else {
                    globalLabel.style.display = "none";
                }
            }
        }
    },

    logout(expired = false) {
        // 1. Cancela todos os intervalos, inatividade e reconexões
        if (state.syncInterval) clearInterval(state.syncInterval);
        if (state.pingInterval) clearInterval(state.pingInterval);
        if (state.wsReconnectTimer) clearTimeout(state.wsReconnectTimer);
        if (state.clearIdleTimer) state.clearIdleTimer();

        // 2. Limpa dados de autenticação
        localStorage.clear();
        state.token = null;
        state.user = null;
        state.tenant_id = null;

        // 3. Encerra WebSocket sem disparar loop de reconexão
        if (state.ws) {
            try {
                state.ws.onopen = null;
                state.ws.onmessage = null;
                state.ws.onerror = null;
                state.ws.onclose = null;
                state.ws.close();
            } catch (e) {}
            state.ws = null;
        }
        updateWsStatusBadge("disconnected");

        // 4. Oculta o layout principal imediatamente
        const mainLayout = document.getElementById("main-layout");
        if (mainLayout) {
            mainLayout.classList.add("layout-hidden");
            mainLayout.style.display = "none";
        }

        // 5. Exibe a tela de login instantaneamente (sem precisar de F5)
        document.querySelectorAll(".view").forEach(v => v.classList.remove("active"));
        const loginView = document.getElementById("login-view");
        if (loginView) {
            loginView.classList.add("active");
            loginView.style.display = "block";
        }

        // 6. Retoma o vídeo de fundo do login
        const loginVid = document.querySelector("#login-view video");
        if (loginVid) {
            try { loginVid.play(); } catch(e) {}
        }

        if (expired) {
            try { showToast("Sessão expirada. Por favor, faça login novamente.", "warning"); } catch(e) {}
        }
    },

    // --- Data Loaders ---
    async loadConversations(status, silent = false) {
        try {
            const listContainer = document.getElementById("convo-list");
            if (!listContainer) return;
            
            const activeTab = document.querySelector(".inbox-tabs .tab-btn.active");
            const statusFilter = status || (activeTab ? activeTab.getAttribute("data-status") : "waiting");
            
            // Renderização instantânea de 0ms a partir do cache local se disponível
            if (!state.conversationsCache) state.conversationsCache = {};
            if (!silent && state.conversationsCache[statusFilter] && state.conversationsCache[statusFilter].length > 0) {
                // Renderiza imediatamente sem esperar pela requisição HTTP
                state.conversations = state.conversationsCache[statusFilter];
            } else if (!silent && (!listContainer.children || listContainer.children.length === 0)) {
                listContainer.innerHTML = "<p class='subtitle' style='padding: 20px;'>Carregando...</p>";
            }
            
            const convos = await api.get(`/api/inbox/conversations?status_filter=${statusFilter}`);
            state.conversations = convos;
            state.conversationsCache[statusFilter] = convos;
            
            if (!silent && convos.length === 0) {
                listContainer.innerHTML = "<p class='subtitle' style='padding: 20px;'>Nenhuma conversa nesta aba.</p>";
                return;
            }

            // Remove o indicador de carregando se não for silencioso
            if (!silent && (listContainer.innerHTML.includes("Carregando...") || listContainer.children.length === 0)) {
                listContainer.innerHTML = "";
            }

            convos.forEach((c, index) => {
                let item = listContainer.querySelector(`[data-id="${c.id}"]`);
                const isUnread = c.unread_count && c.unread_count > 0 && state.activeConversationId !== c.id;
                const needsResponse = (c.last_message_sender_type === 'contact') && (state.activeConversationId !== c.id);
                const contactName = c.contact ? c.contact.name || c.contact.phone_number : "Hóspede";
                
                const avatarUrl = (c.contact && c.contact.avatar_url)
                    ? c.contact.avatar_url
                    : `https://api.dicebear.com/7.x/initials/svg?seed=${encodeURIComponent(contactName)}`;
                
                let previewText = "";
                if (c.last_message_body) {
                    let cleanText = c.last_message_body;
                    if (cleanText === "[Unsupported]" || cleanText.toLowerCase().includes("unsupported")) {
                        cleanText = "ℹ️ [Mensagem de Sistema / Auth]";
                    }
                    const prefix = c.last_message_sender_type === 'bot' ? '🤖 ' : c.last_message_sender_type === 'agent' ? '✍️ ' : '';
                    previewText = prefix + cleanText.substring(0, 50);
                } else {
                    if (c.status === "waiting") previewText = "Aguardando atendimento";
                    else if (c.status === "bot") previewText = "Em atendimento pelo bot";
                    else if (c.status === "active") previewText = "Em atendimento";
                    else previewText = "Conversa finalizada";
                }

                const unreadBadge = isUnread
                    ? `<span class="unread-badge" style="background-color: var(--color-primary); color: white; border-radius: 50%; font-size: 10px; font-weight: 700; min-width: 18px; height: 18px; padding: 0 4px; display: inline-flex; align-items: center; justify-content: center; margin-left: 8px; box-shadow: 0 0 4px var(--color-primary);">${c.unread_count}</span>`
                    : '';

                const awaitingBadge = needsResponse
                    ? `<span class="awaiting-badge" title="Cliente aguardando resposta"><i class="fa-solid fa-clock"></i> Pendente</span>`
                    : '';

                let flagColor = "transparent";
                if (c.flag_type === "red") flagColor = "#ef4444";
                else if (c.flag_type === "yellow") flagColor = "#fbbf24";
                else if (c.flag_type === "blue") flagColor = "#3b82f6";
                else if (c.flag_type === "green") flagColor = "#10b981";

                const flagIcon = c.flag_type && c.flag_type !== "none"
                    ? `<i class="fa-solid fa-flag" style="color: ${flagColor}; margin-left: 6px; font-size: 11px;"></i>`
                    : '';

                const timeStr = formatRelativeTime(c.last_message_at);

                if (!item) {
                    item = document.createElement("div");
                    item.setAttribute("data-id", c.id);
                    item.onclick = () => this.selectConversation(c.id);
                    listContainer.appendChild(item);
                }

                const itemHash = `${contactName}_${c.last_message_at}_${previewText}_${isUnread}_${needsResponse}_${c.flag_type}_${state.activeConversationId === c.id}`;
                if (item.getAttribute("data-hash") !== itemHash) {
                    item.setAttribute("data-hash", itemHash);
                    item.className = `convo-item ${state.activeConversationId === c.id ? 'active' : ''} ${isUnread ? 'unread' : ''} ${needsResponse ? 'needs-response' : ''}`;
                    item.innerHTML = `
                        <img class="avatar" src="${avatarUrl}" alt="${contactName}" onerror="this.onerror=null; this.src='favicon.png';">
                        <div class="convo-meta">
                            <h4>
                                <span style="display: flex; align-items: center; gap: 2px; min-width: 0; overflow: hidden;">
                                    <span style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${contactName}</span>
                                    ${awaitingBadge}
                                    ${flagIcon}
                                    ${unreadBadge}
                                </span>
                                <span class="convo-time">${timeStr}</span>
                            </h4>
                            <p style="overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 12px; opacity: 0.7;">${previewText}</p>
                        </div>
                    `;
                }
                
                // Mantém a ordem sincronizada com a API (chats mais recentes no topo)
                if (listContainer.children[index] !== item) {
                    listContainer.insertBefore(item, listContainer.children[index]);
                }
            });

            // Remove itens que não fazem mais parte do filtro da aba atual
            const convoIds = convos.map(c => c.id);
            Array.from(listContainer.children).forEach(child => {
                const id = child.getAttribute("data-id");
                if (id && !convoIds.includes(id)) {
                    child.remove();
                }
            });

            // Re-aplica busca se houver texto digitado no filtro de pesquisa
            const searchInput = document.getElementById("convo-search-input");
            if (searchInput && searchInput.value.trim() !== "") {
                this.filterConversations(searchInput.value);
            }
        } catch (e) {
            console.error("Erro ao carregar conversas:", e);
        }
    },

    filterConversations(query) {
        const term = (query || "").toLowerCase().trim();
        const listContainer = document.getElementById("convo-list");
        if (!listContainer) return;

        const items = listContainer.querySelectorAll(".convo-item");
        let visibleCount = 0;

        items.forEach(item => {
            const convoId = item.getAttribute("data-id");
            const convo = (state.conversations || []).find(c => c.id === convoId);

            if (!term) {
                item.style.display = "flex";
                visibleCount++;
                return;
            }

            let match = false;
            if (convo) {
                const name = (convo.contact && convo.contact.name) ? convo.contact.name.toLowerCase() : "";
                const phone = (convo.contact && convo.contact.phone_number) ? convo.contact.phone_number.toLowerCase() : "";

                if (name.includes(term) || phone.includes(term)) {
                    match = true;
                }
            } else {
                // Fallback via cabeçalho do elemento (somente nome do contato)
                const titleEl = item.querySelector("h4");
                const nameText = titleEl ? titleEl.innerText.toLowerCase() : "";
                if (nameText.includes(term)) match = true;
            }

            if (match) {
                item.style.display = "flex";
                visibleCount++;
            } else {
                item.style.display = "none";
            }
        });

        // Feedback se nenhum contato for encontrado
        let emptyMsg = listContainer.querySelector(".convo-search-empty");
        if (visibleCount === 0 && term !== "") {
            if (!emptyMsg) {
                emptyMsg = document.createElement("p");
                emptyMsg.className = "subtitle convo-search-empty";
                emptyMsg.style.cssText = "padding: 20px; text-align: center; opacity: 0.7; font-size: 13px;";
                listContainer.appendChild(emptyMsg);
            }
            emptyMsg.innerText = `Nenhum contato encontrado para "${query}".`;
            emptyMsg.style.display = "block";
        } else if (emptyMsg) {
            emptyMsg.style.display = "none";
        }
    },

    async selectConversation(convoId) {
        state.activeConversationId = convoId;
        
        // Limpa o painel de mensagens instantaneamente (0ms) para evitar exibição da conversa anterior
        const scroll = document.getElementById("message-scroll");
        if (scroll) {
            if (state.messagesCache && state.messagesCache[convoId] && state.messagesCache[convoId].length > 0) {
                scroll.innerHTML = "";
                state.messagesCache[convoId].forEach(m => {
                    const bubble = renderMessageBubble(m);
                    scroll.appendChild(bubble);
                });
                scroll.scrollTop = scroll.scrollHeight;
            } else {
                scroll.innerHTML = `<div style="text-align:center;padding:40px;opacity:0.5;font-size:13px;"><i class="fa-solid fa-spinner fa-spin"></i> Carregando mensagens...</div>`;
            }
        }

        // Marca item como ativo na lista e limpa o destaque visual (Pendente e Não Lido)
        document.querySelectorAll(".convo-item").forEach(item => {
            item.classList.remove("active");
            if (item.getAttribute("data-id") === convoId) {
                item.classList.add("active");
                item.classList.remove("unread");
                item.classList.remove("needs-response");
                const badge = item.querySelector(".unread-badge");
                if (badge) badge.remove();
                const awaitingBadge = item.querySelector(".awaiting-badge");
                if (awaitingBadge) awaitingBadge.remove();
            }
        });
        
        let convo = state.conversations.find(c => c.id === convoId);
        if (convo) {
            convo.unread = false;
            convo.unread_count = 0;
        }
        
        // Mostrar painel de chat imediatamente (antes de carregar dados)
        const activeArea = document.getElementById("active-chat-area");
        activeArea.classList.remove("empty");
        activeArea.querySelector(".no-chat-selected").style.display = "none";
        activeArea.querySelector(".chat-wrapper").style.display = "flex";

        // Painel de contexto
        const guestContext = document.getElementById("guest-context");
        const layout = document.querySelector(".inbox-layout");
        const showContextBtn = document.getElementById("btn-show-context");
        if (state.isContextPanelVisible === undefined) state.isContextPanelVisible = true;
        if (state.isContextPanelVisible) {
            guestContext.style.display = "block";
            if (layout) layout.classList.remove("hide-context");
            if (showContextBtn) showContextBtn.style.display = "none";
        } else {
            guestContext.style.display = "none";
            if (layout) layout.classList.add("hide-context");
            if (showContextBtn) showContextBtn.style.display = "block";
        }

        // Se não tiver contato no state, buscar da API
        if (!convo || !convo.contact) {
            try {
                convo = await api.get(`/api/inbox/conversations/${convoId}/detail`);
                // Atualiza no state também
                const idx = state.conversations.findIndex(c => c.id === convoId);
                if (idx >= 0) state.conversations[idx] = convo;
                else state.conversations.push(convo);
            } catch (e) {
                console.warn("[inbox] Falha ao buscar detalhes da conversa:", e);
            }
        }

        // Atualizar header e painel lateral com dados do contato
        if (convo && convo.contact) {
            const contactName = convo.contact.name || convo.contact.phone_number || "Hóspede";
            document.getElementById("active-contact-name").innerText = contactName;
            
            const nameInput = document.getElementById("guest-name-input");
            if (nameInput) nameInput.value = convo.contact.name || "";
            
            document.getElementById("guest-phone").innerText = convo.contact.phone_number || "";
            const lang = convo.contact.language;
            document.getElementById("guest-lang").innerText = lang === "pt-BR" || lang === "pt_BR" ? "Português" : (lang || "—");
            
            const loyalty = convo.contact.loyalty_level || "none";
            document.getElementById("guest-loyalty").innerText = loyalty.charAt(0).toUpperCase() + loyalty.slice(1);
            
            const stageLabels = {
                "lead": "Lead / Novo",
                "qualified": "Qualificado",
                "quotation": "Orçamento Enviado",
                "reservation_pending": "Reserva Pendente",
                "reservation_confirmed": "Reserva Confirmada",
                "lost": "Perdido"
            };
            const stage = convo.contact.sales_funnel_stage;
            document.getElementById("guest-funnel-stage").innerText = stageLabels[stage] || (stage || "—").toUpperCase();

            const avatarUrl = convo.contact.avatar_url || `https://api.dicebear.com/7.x/initials/svg?seed=${encodeURIComponent(contactName)}`;
            const activeAvatar = document.getElementById("active-avatar");
            if (activeAvatar) {
                activeAvatar.innerHTML = `<img class="avatar" src="${avatarUrl}" alt="${contactName}" style="width:40px;height:40px;border-radius:50%;object-fit:cover;" onerror="this.onerror=null; this.src='favicon.png';">`;
            }

            // Flag
            const flagToggle = document.getElementById("chat-flag-toggle");
            if (flagToggle) {
                let color = "var(--text-muted)";
                if (convo.flag_type === "red") color = "#ef4444";
                else if (convo.flag_type === "yellow") color = "#fbbf24";
                else if (convo.flag_type === "blue") color = "#3b82f6";
                else if (convo.flag_type === "green") color = "#10b981";
                flagToggle.style.color = color;
            }

            // Renderiza as Etiquetas/Tags da Conversa Ativa
            const tagsListEl = document.getElementById("active-chat-tags-list");
            if (tagsListEl) {
                tagsListEl.innerHTML = "";
                if (convo.tags && convo.tags.length > 0) {
                    convo.tags.forEach(t => {
                        const tagBadge = document.createElement("span");
                        tagBadge.className = "tag-badge";
                        tagBadge.style.background = t.color || "#6366f1";
                        tagBadge.innerHTML = `
                            <span>${escapeHTML(t.name)}</span>
                            <i class="fa-solid fa-xmark tag-remove-btn" title="Remover tag" onclick="event.stopPropagation(); uiHelpers.removeTagFromActiveConvo('${t.id}')"></i>
                        `;
                        tagsListEl.appendChild(tagBadge);
                    });
                }
            }
        } else if (convo) {
            // Sem contato mas tem conversa — usar ID como fallback
            document.getElementById("active-contact-name").innerText = "Contato Desconhecido";
        }

        // Atualizar status no header
        if (convo) {
            const statusEl = document.getElementById("active-contact-status");
            if (statusEl) {
                const statusMap = {
                    "waiting": "⏳ Aguardando Atendente",
                    "bot": "🤖 Em Atendimento pelo Bot",
                    "active": "✅ Em Atendimento",
                    "resolved": "✔ Resolvida"
                };
                let statusText = statusMap[convo.status] || convo.status;
                if (convo.csat_score) {
                    statusText += ` • ⭐ CSAT ${convo.csat_score}/5`;
                }
                statusEl.innerText = statusText;
            }

            // Botão Assumir vs Transferir
            const transferBtn = document.getElementById("btn-transfer-chat");
            if (transferBtn) {
                if (convo.status === "waiting" || convo.status === "bot") {
                    transferBtn.innerHTML = `<i class="fa-solid fa-headset"></i> Assumir Atendimento`;
                    transferBtn.className = "btn btn-primary btn-sm";
                } else {
                    transferBtn.innerHTML = `<i class="fa-solid fa-arrow-right-arrow-left"></i> Transferir`;
                    transferBtn.className = "btn btn-secondary btn-sm";
                }
            }

            // Botão Enviar para o Bot
            const transferToBotBtn = document.getElementById("btn-transfer-to-bot");
            if (transferToBotBtn) {
                transferToBotBtn.style.display = convo.status === "bot" ? "none" : "inline-flex";
            }
        }

        // Mostrar / Ocultar campo de texto com base na janela de 24h
        const chatInputForm = document.getElementById("chat-input-form");
        const chatBlockedArea = document.getElementById("chat-blocked-area");
        
        if (chatInputForm && chatBlockedArea) {
            if (convo && convo.has_active_window) {
                chatInputForm.style.display = "flex";
                chatBlockedArea.style.display = "none";
            } else {
                chatInputForm.style.display = "none";
                chatBlockedArea.style.display = "flex";
            }
        }

        // Carregar mensagens (com cache em memória para exibição INSTANTÂNEA em 0ms)
        try {
            const scroll = document.getElementById("message-scroll");
            if (scroll) {
                if (state.messagesCache && state.messagesCache[convoId] && state.messagesCache[convoId].length > 0) {
                    scroll.innerHTML = "";
                    state.messagesCache[convoId].forEach(m => {
                        const bubble = renderMessageBubble(m);
                        scroll.appendChild(bubble);
                    });
                    scroll.scrollTop = scroll.scrollHeight;
                } else {
                    scroll.innerHTML = `<div style="text-align:center;padding:20px;opacity:0.5;font-size:13px;">Carregando mensagens...</div>`;
                }
            }

            const messages = await api.get(`/api/inbox/conversations/${convoId}/messages`);
            if (!state.messagesCache) state.messagesCache = {};
            state.messagesCache[convoId] = messages;

            // Limita o cache de mensagens em memória RAM para no máximo 15 chats simultâneos (previne Memory Leak em sessões longas)
            const cacheKeys = Object.keys(state.messagesCache);
            if (cacheKeys.length > 15) {
                const oldestKey = cacheKeys.find(k => k !== state.activeConversationId && k !== convoId);
                if (oldestKey) delete state.messagesCache[oldestKey];
            }

            if (scroll) {
                scroll.innerHTML = "";
                if (messages.length === 0) {
                    scroll.innerHTML = `<div style="text-align:center;padding:40px;opacity:0.5;font-size:13px;">Nenhuma mensagem ainda.</div>`;
                } else {
                    messages.forEach(m => {
                        const bubble = renderMessageBubble(m);
                        scroll.appendChild(bubble);
                    });
                    scroll.scrollTop = scroll.scrollHeight;
                }
            }
        } catch (e) {
            console.error("[inbox] Erro ao carregar mensagens:", e);
            const scroll = document.getElementById("message-scroll");
            if (scroll && (!state.messagesCache || !state.messagesCache[convoId])) {
                scroll.innerHTML = `<div style="text-align:center;padding:20px;color:var(--color-danger);font-size:13px;">Erro ao carregar histórico da conversa: ${e.message}</div>`;
            }
        }
    },

    async sendReactivationMessage() {
        const convoId = state.activeConversationId;
        if (!convoId) {
            showToast("Nenhuma conversa selecionada", "warning");
            return;
        }

        // Garante que os templates estão carregados
        if (!state.messageTemplates || state.messageTemplates.length === 0) {
            await this.loadTemplates();
        }

        this.openTemplatePreviewModal();
    },

    openTemplatePreviewModal() {
        const modal = document.getElementById("template-preview-modal");
        const select = document.getElementById("template-preview-select");
        const bodyPreview = document.getElementById("template-preview-body-text");
        const catBadge = document.getElementById("template-preview-category-badge");
        if (!modal || !select) return;

        const templates = state.messageTemplates || [];
        if (templates.length === 0) {
            showToast("Nenhum modelo de mensagem sincronizado da Meta. Sincronize na aba Configurações.", "warning");
            return;
        }

        const activeConvo = state.conversations.find(c => c.id === state.activeConversationId);
        const contactName = (activeConvo && activeConvo.contact && activeConvo.contact.name) ? activeConvo.contact.name : "Cliente";

        select.innerHTML = "";
        templates.forEach(t => {
            const opt = document.createElement("option");
            opt.value = t.name;
            opt.innerText = `${t.label || t.name} (${t.name}) - [${t.category || "UTILITY"}]`;
            select.appendChild(opt);
        });

        const updatePreview = () => {
            const selectedName = select.value;
            const tpl = templates.find(t => t.name === selectedName);
            if (tpl) {
                let previewText = tpl.body_text || `[Modelo: ${tpl.name}]`;
                // Substitui variáveis {{1}} ou {{contact_name}} com o nome real do cliente
                previewText = previewText.replace(/\{\{1\}\}/g, contactName)
                                         .replace(/\{\{contact_name\}\}/gi, contactName)
                                         .replace(/\{\{nome\}\}/gi, contactName);
                bodyPreview.innerText = previewText;
                catBadge.innerText = tpl.category || "UTILITY";
                if (tpl.category === "MARKETING") {
                    catBadge.style.background = "rgba(59,130,246,0.15)";
                    catBadge.style.color = "#60a5fa";
                } else {
                    catBadge.style.background = "rgba(16,185,129,0.15)";
                    catBadge.style.color = "#34d399";
                }
            }
        };

        select.onchange = updatePreview;
        updatePreview();

        modal.style.display = "flex";
    },

    closeTemplatePreviewModal() {
        const modal = document.getElementById("template-preview-modal");
        if (modal) modal.style.display = "none";
    },

    async confirmSendTemplate() {
        const convoId = state.activeConversationId;
        const select = document.getElementById("template-preview-select");
        const sendBtn = document.getElementById("btn-confirm-send-template");
        if (!convoId || !select) return;

        const chosenTemplate = select.value;
        const templates = state.messageTemplates || [];
        const tpl = templates.find(t => t.name === chosenTemplate);
        const chosenLang = (tpl && tpl.language) ? tpl.language : "pt_BR";

        const originalBtnHtml = sendBtn ? sendBtn.innerHTML : "";
        try {
            if (sendBtn) {
                sendBtn.disabled = true;
                sendBtn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Enviando...`;
            }

            await api.post("/api/inbox/send-message", {
                conversation_id: convoId,
                template_name: chosenTemplate,
                template_language: chosenLang
            });

            this.closeTemplatePreviewModal();
            showToast(`Mensagem enviada com sucesso (Modelo: ${chosenTemplate})!`, "success");
            await this.loadConversation(convoId);
        } catch (err) {
            console.error("Erro ao enviar modelo:", err);
            showToast("Erro ao enviar modelo: " + (err.message || err), "error");
        } finally {
            if (sendBtn) {
                sendBtn.disabled = false;
                sendBtn.innerHTML = originalBtnHtml;
            }
        }
    },


    async loadMetaSettings() {
        if (state.user && state.user.role !== "administrator") {
            return;
        }
        try {
            const creds = await api.get("/api/auth/meta-credentials");
            if (creds) {
                document.getElementById("phone-number-id").value = creds.phone_number_id || "";
                document.getElementById("waba-id").value = creds.waba_id || "";
                document.getElementById("verify-token").value = creds.verify_token || "";
                document.getElementById("permanent-token").value = creds.permanent_access_token || "";
                document.getElementById("webhook-generated-url").innerText = creds.webhook_url;
            }
        } catch (e) {
            document.getElementById("webhook-generated-url").innerText = `${API_URL}/api/webhook/${state.tenant_id}`;
        }

        try {
            const botConfig = await api.get("/api/inbox/bot-config");
            if (botConfig) {
                document.getElementById("n8n-webhook-url").value = botConfig.n8n_webhook_url || "";
            }
        } catch (e) {
            // Silently ignore if botConfig isn't loaded
        }

        // Carrega os dados de faturamento do hotel
        this.loadBillingSummary();
    },

    async loadBotConfig() {
        try {
            const config = await api.get("/api/inbox/bot-config");
            if (config) {
                document.getElementById("bot-active-toggle").checked = config.is_active;
                document.getElementById("bot-welcome-msg").value = config.welcome_message || "";
                document.getElementById("bot-fallback-msg").value = config.fallback_message || "";
                document.getElementById("bot-out-hours-msg").value = config.out_of_hours_message || "";
                document.getElementById("bot-transfer-keywords").value = config.transfer_keywords || "";
                
                // Sync preview text
                const previewText = document.getElementById("preview-bot-welcome-text");
                if (previewText) {
                    previewText.innerHTML = formatMessageBody(config.welcome_message || "Olá! Bem-vindo ao nosso hotel...");
                }
            }
        } catch (e) {
            console.error("Erro ao carregar configurações do chatbot:", e);
        }
    },

    // Holds Chart instances for clean destruction and re-creation
    dashboardCharts: {
        traffic: null,
        status: null
    },

    async loadDashboardMetrics(isManual = false) {
        const refreshBtn = document.getElementById("btn-refresh-dashboard");
        const iconEl = refreshBtn ? refreshBtn.querySelector("i") : null;
        if (isManual && iconEl) {
            iconEl.classList.add("fa-spin");
            if (refreshBtn) refreshBtn.disabled = true;
        }

        try {
            const metrics = await api.get("/api/inbox/dashboard-metrics");
            
            // 1. Populate 6 Real KPI Cards
            const setVal = (id, val) => {
                const el = document.getElementById(id);
                if (el) el.innerText = val !== undefined && val !== null ? val : 0;
            };

            setVal("stat-conversations", metrics.total_conversations || 0);
            setVal("stat-waiting", metrics.waiting_conversations || 0);
            setVal("stat-active", metrics.active_conversations || 0);
            setVal("stat-bot", metrics.bot_conversations || 0);
            setVal("stat-contacts", metrics.total_contacts || 0);
            setVal("stat-messages-today", metrics.messages_today || 0);

            // Update Date / Timestamp Badge
            const dateBadge = document.getElementById("current-date");
            if (dateBadge) {
                const now = new Date();
                const timeStr = `${String(now.getHours()).padStart(2,'0')}:${String(now.getMinutes()).padStart(2,'0')}`;
                dateBadge.innerText = `Hoje às ${timeStr}`;
            }

            // Update Conversion Rate Badge
            const convBadge = document.getElementById("stat-conversion-rate-badge");
            if (convBadge) {
                convBadge.innerText = `${metrics.conversion_rate || 0}% Conversão`;
            }

            // 2. Render Real Chart 1: Daily Traffic Chart (Line Chart with Gradients)
            const ChartConstructor = window.Chart || (typeof Chart !== "undefined" ? Chart : null);
            const trafficCanvas = document.getElementById("dashboard-traffic-chart");
            if (trafficCanvas && ChartConstructor && metrics.daily_traffic) {
                if (this.dashboardCharts.traffic) {
                    try { this.dashboardCharts.traffic.destroy(); } catch(e){}
                    this.dashboardCharts.traffic = null;
                }

                const labels = metrics.daily_traffic.map(d => d.date);
                const incomingData = metrics.daily_traffic.map(d => d.incoming_count);
                const outgoingData = metrics.daily_traffic.map(d => d.outgoing_count);

                const ctx = trafficCanvas.getContext("2d");
                this.dashboardCharts.traffic = new ChartConstructor(ctx, {
                    type: "line",
                    data: {
                        labels: labels,
                        datasets: [
                            {
                                label: "Recebidas (Clientes)",
                                data: incomingData,
                                borderColor: "#3b82f6",
                                backgroundColor: "rgba(59, 130, 246, 0.15)",
                                fill: true,
                                tension: 0.35,
                                borderWidth: 2.5,
                                pointBackgroundColor: "#3b82f6",
                                pointRadius: 4
                            },
                            {
                                label: "Enviadas (Equipe / Bot)",
                                data: outgoingData,
                                borderColor: "#22c55e",
                                backgroundColor: "rgba(34, 197, 94, 0.10)",
                                fill: true,
                                tension: 0.35,
                                borderWidth: 2.5,
                                pointBackgroundColor: "#22c55e",
                                pointRadius: 4
                            }
                        ]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: {
                            legend: {
                                display: true,
                                position: "top",
                                labels: {
                                    boxWidth: 12,
                                    font: { size: 11, family: "Inter, sans-serif" },
                                    color: "#94a3b8"
                                }
                            },
                            tooltip: {
                                mode: "index",
                                intersect: false
                            }
                        },
                        scales: {
                            x: {
                                grid: { color: "rgba(255,255,255,0.04)" },
                                ticks: { font: { size: 10 }, color: "#94a3b8" }
                            },
                            y: {
                                beginAtZero: true,
                                grid: { color: "rgba(255,255,255,0.04)" },
                                ticks: { precision: 0, font: { size: 10 }, color: "#94a3b8" }
                            }
                        }
                    }
                });
            }

            // 3. Render Real Chart 2: Status Breakdown (Doughnut Chart)
            const statusCanvas = document.getElementById("dashboard-status-chart");
            if (statusCanvas && ChartConstructor) {
                if (this.dashboardCharts.status) {
                    try { this.dashboardCharts.status.destroy(); } catch(e){}
                    this.dashboardCharts.status = null;
                }

                const waitingCount = metrics.waiting_conversations || 0;
                const activeCount = metrics.active_conversations || 0;
                const botCount = metrics.bot_conversations || 0;
                const resolvedCount = metrics.resolved_conversations || 0;

                const hasData = (waitingCount + activeCount + botCount + resolvedCount) > 0;
                const statusCtx = statusCanvas.getContext("2d");

                this.dashboardCharts.status = new ChartConstructor(statusCtx, {
                    type: "doughnut",
                    data: {
                        labels: hasData 
                            ? ["Fila de Espera", "Em Atendimento", "Com Robô", "Resolvidas"] 
                            : ["Sem atendimentos no momento"],
                        datasets: [{
                            data: hasData 
                                ? [waitingCount, activeCount, botCount, resolvedCount] 
                                : [1],
                            backgroundColor: hasData 
                                ? ["#f59e0b", "#3b82f6", "#a855f7", "#22c55e"]
                                : ["#334155"],
                            borderWidth: 0,
                            hoverOffset: 4
                        }]
                    },
                    options: {
                        responsive: true,
                        maintainAspectRatio: false,
                        plugins: {
                            legend: {
                                position: "right",
                                labels: {
                                    boxWidth: 12,
                                    font: { size: 11, family: "Inter, sans-serif" },
                                    color: "#94a3b8"
                                }
                            }
                        },
                        cutout: "70%"
                    }
                });
            }

            // 4. Populate Real CRM Funnel
            const funnelContainer = document.getElementById("funnel-container-stats");
            if (funnelContainer && metrics.funnel_stages) {
                funnelContainer.innerHTML = "";
                const colors = ["#6366f1", "#f59e0b", "#22c55e"];
                metrics.funnel_stages.forEach((stage, idx) => {
                    const row = document.createElement("div");
                    row.style.cssText = "display: flex; flex-direction: column; gap: 4px;";
                    row.innerHTML = `
                        <div style="display: flex; justify-content: space-between; font-size: 12px; font-weight: 600;">
                            <span>${stage.stage}</span>
                            <span style="color: var(--text-muted);">${stage.count} contatos (${stage.percentage}%)</span>
                        </div>
                        <div style="background: rgba(255,255,255,0.05); height: 8px; border-radius: 4px; overflow: hidden;">
                            <div style="background: ${colors[idx % colors.length]}; width: ${Math.max(stage.percentage, 2)}%; height: 100%; border-radius: 4px; transition: width 0.6s ease;"></div>
                        </div>
                    `;
                    funnelContainer.appendChild(row);
                });
            }

            // 5. Populate Real Team Productivity Table
            const teamBody = document.getElementById("dashboard-team-body");
            if (teamBody && metrics.team_performance) {
                teamBody.innerHTML = "";
                if (metrics.team_performance.length === 0) {
                    teamBody.innerHTML = "<tr><td colspan='4' style='text-align: center; padding: 20px; opacity: 0.6;'>Nenhum atendente cadastrado.</td></tr>";
                } else {
                    metrics.team_performance.forEach(agent => {
                        const tr = document.createElement("tr");
                        tr.style.cssText = "border-bottom: 1px solid var(--border-color);";
                        tr.innerHTML = `
                            <td style="padding: 10px 6px;">
                                <div style="font-weight: 600; color: var(--text-primary);">${agent.name}</div>
                                <div style="font-size: 10px; color: var(--text-muted);">${agent.email}</div>
                            </td>
                            <td style="padding: 10px 6px;">
                                <span style="font-size: 10px; padding: 2px 6px; border-radius: 6px; background: rgba(99,102,241,0.12); color: #818cf8; font-weight: 600;">${agent.role}</span>
                            </td>
                            <td style="padding: 10px 6px; text-align: center; font-weight: 700; color: #3b82f6;">${agent.active_count}</td>
                            <td style="padding: 10px 6px; text-align: center; font-weight: 700; color: #22c55e;">${agent.resolved_count}</td>
                        `;
                        teamBody.appendChild(tr);
                    });
                }
            }

            if (isManual) {
                showToast("Painel atualizado com dados em tempo real!", "success");
            }
        } catch (e) {
            console.error("Erro ao carregar métricas do painel:", e);
            if (isManual) {
                showToast("Erro ao atualizar métricas do painel.", "error");
            }
        } finally {
            if (iconEl) iconEl.classList.remove("fa-spin");
            if (refreshBtn) refreshBtn.disabled = false;
        }
    },

    async loadTeamUsers() {
        try {
            const tableBody = document.getElementById("team-users-list");
            tableBody.innerHTML = "<tr><td colspan='5' style='padding: 20px; text-align: center;'>Carregando colaboradores...</td></tr>";
            
            const users = await api.get("/api/auth/users");
            tableBody.innerHTML = "";
            
            if (users.length === 0) {
                tableBody.innerHTML = "<tr><td colspan='5' style='padding: 20px; text-align: center;'>Nenhum colaborador ativo.</td></tr>";
                return;
            }
            
            users.forEach(u => {
                const tr = document.createElement("tr");
                
                // Role translation badge
                let roleLabel = "";
                let roleClass = "";
                if (u.role === "administrator") {
                    roleLabel = "Administrador";
                    roleClass = "background: rgba(79, 70, 229, 0.1); color: var(--color-brand);";
                } else if (u.role === "manager") {
                    roleLabel = "Supervisor";
                    roleClass = "background: rgba(2, 132, 199, 0.1); color: var(--color-info);";
                } else {
                    roleLabel = "Vendedor";
                    roleClass = "background: rgba(13, 148, 136, 0.1); color: var(--color-success);";
                }
                
                // Status translation badge
                let statusLabel = "";
                let statusColor = "";
                if (u.status === "online") {
                    statusLabel = "Online";
                    statusColor = "var(--color-success)";
                } else if (u.status === "busy") {
                    statusLabel = "Ocupado";
                    statusColor = "var(--color-warning)";
                } else {
                    statusLabel = "Offline";
                    statusColor = "var(--text-muted)";
                }
                
                // Exclude Action Button (disabled for self-deletion)
                const isSelf = u.id === state.user.id;
                const isMainAdminToDelete = u.role === "administrator" && state.user.role !== "administrator";
                const canDelete = !isSelf && !isMainAdminToDelete;
                
                const actionButton = canDelete 
                    ? `<button class="btn btn-secondary btn-sm btn-delete-user" data-id="${u.id}" style="border-color: var(--color-danger); color: var(--color-danger); background: transparent;">Excluir</button>`
                    : `<span class="subtitle" style="font-size: 11px;">Restrito</span>`;
                
                tr.innerHTML = `
                    <td><strong>${u.name}</strong></td>
                    <td>${u.email}</td>
                    <td><span class="badge" style="${roleClass} font-weight: 700;">${roleLabel}</span></td>
                    <td><span style="display: inline-flex; align-items: center; gap: 6px; font-size: 13px; font-weight: 600;"><span style="width: 8px; height: 8px; border-radius: 50%; background-color: ${statusColor}; display: inline-block;"></span>${statusLabel}</span></td>
                    <td>${actionButton}</td>
                `;
                tableBody.appendChild(tr);
            });

            // Bind delete buttons dynamically to prevent global window scope issues
            tableBody.querySelectorAll(".btn-delete-user").forEach(btn => {
                btn.addEventListener("click", async (e) => {
                    const userId = e.currentTarget.getAttribute("data-id");
                    await this.deleteTeamUser(userId);
                });
            });
        } catch (e) {
            console.error(e);
            document.getElementById("team-users-list").innerHTML = `<tr><td colspan='5' style='padding: 20px; text-align: center; color: var(--color-danger);'>Erro ao carregar colaboradores: ${e.message}</td></tr>`;
        }
    },

    async deleteTeamUser(userId) {
        try {
            await api.delete(`/api/auth/users/${userId}`);
            showToast("Colaborador removido com sucesso!", "success");
            this.loadTeamUsers();
        } catch (e) {
            showToast("Erro ao remover colaborador: " + e.message, "error");
        }
    },

    async loadQuickMessages() {
        try {
            const listEl = document.getElementById("quick-messages-list");
            if (!listEl) return;
            listEl.innerHTML = "<tr><td colspan='4' style='padding: 10px; text-align: center;'>Carregando...</td></tr>";

            const quickMsgs = await api.get("/api/inbox/quick-messages");
            state.quickMessages = quickMsgs;

            listEl.innerHTML = "";
            if (quickMsgs.length === 0) {
                listEl.innerHTML = "<tr><td colspan='4' style='padding: 20px; text-align: center; opacity: 0.5;'>Nenhuma resposta rápida cadastrada.</td></tr>";
                return;
            }

            quickMsgs.forEach(qm => {
                const tr = document.createElement("tr");
                const typeLabel = qm.is_global ? "Global" : "Pessoal";
                const typeClass = qm.is_global ? "scope-badge global" : "scope-badge personal";

                tr.innerHTML = `
                    <td><strong>/${qm.shortcut}</strong></td>
                    <td style="max-width: 400px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;">${qm.body}</td>
                    <td><span class="${typeClass}" style="font-weight: 700; font-size: 11px;">${typeLabel}</span></td>
                    <td><button class="btn btn-secondary btn-sm btn-delete-quick" data-id="${qm.id}" style="border-color: var(--color-danger); color: var(--color-danger); background: transparent; padding: 6px 12px; font-size: 11px;">Excluir</button></td>
                `;
                listEl.appendChild(tr);
            });

            // Bind click to delete buttons
            listEl.querySelectorAll(".btn-delete-quick").forEach(btn => {
                btn.addEventListener("click", async (e) => {
                    const id = e.currentTarget.getAttribute("data-id");
                    try {
                        await api.delete(`/api/inbox/quick-messages/${id}`);
                        showToast("Resposta rápida excluída!", "success");
                        appRouter.loadQuickMessages();
                    } catch (err) {
                        showToast("Erro ao excluir: " + err.message, "error");
                    }
                });
            });

        } catch (e) {
            console.error(e);
        }
    },

    async loadTemplates() {
        try {
            const listEl = document.getElementById("templates-list-tbody");
            const selectEl = document.getElementById("start-chat-template-select");

            let templates = [];
            try {
                templates = await api.get("/api/inbox/templates");
            } catch (err) {
                console.warn("[Templates Notice] Usando template padrão de fallback:", err);
                templates = [{
                    id: "00000000-0000-0000-0000-000000000001",
                    name: "primeiro_contato",
                    label: "Primeiro Contato - Boas-Vindas",
                    language: "pt_BR",
                    category: "UTILITY"
                }];
            }

            if (!templates || templates.length === 0) {
                templates = [{
                    id: "00000000-0000-0000-0000-000000000001",
                    name: "primeiro_contato",
                    label: "Primeiro Contato - Boas-Vindas",
                    language: "pt_BR",
                    category: "UTILITY"
                }];
            }

            state.messageTemplates = templates;

            // 1. Atualizar Tabela em Configuracoes
            if (listEl) {
                listEl.innerHTML = "";
                if (!templates || templates.length === 0) {
                    listEl.innerHTML = "<tr><td colspan='5' style='padding: 20px; text-align: center; opacity: 0.5;'>Nenhum template cadastrado.</td></tr>";
                } else {
                    templates.forEach(tpl => {
                        const tr = document.createElement("tr");
                        tr.innerHTML = `
                            <td><code>${tpl.name}</code></td>
                            <td><strong>${tpl.label || tpl.name}</strong></td>
                            <td><span style="font-size: 11px; background: var(--bg-tertiary); padding: 2px 6px; border-radius: 4px;">${tpl.language || 'pt_BR'}</span></td>
                            <td><span style="font-size: 11px; font-weight: 700; color: var(--color-brand);">${tpl.category || 'UTILITY'}</span></td>
                            <td style="text-align: right;"><button type="button" class="btn btn-secondary btn-sm btn-delete-template" data-id="${tpl.id}" style="border-color: var(--color-danger); color: var(--color-danger); background: transparent; padding: 4px 8px; font-size: 11px;"><i class="fa-solid fa-trash"></i> Excluir</button></td>
                        `;
                        listEl.appendChild(tr);
                    });

                    listEl.querySelectorAll(".btn-delete-template").forEach(btn => {
                        btn.addEventListener("click", async (e) => {
                            const id = e.currentTarget.getAttribute("data-id");
                            if (confirm("Deseja realmente excluir este modelo de template?")) {
                                try {
                                    await api.delete(`/api/inbox/templates/${id}`);
                                    showToast("Template excluído com sucesso!", "success");
                                    appRouter.loadTemplates();
                                } catch (err) {
                                    showToast("Erro ao excluir template: " + err.message, "error");
                                }
                            }
                        });
                    });
                }
            }

            // 2. Atualizar Dropdown na Modal Iniciar Conversa
            if (selectEl) {
                const currentSelection = selectEl.value;
                selectEl.innerHTML = "";
                if (templates && templates.length > 0) {
                    templates.forEach(tpl => {
                        const opt = document.createElement("option");
                        opt.value = `${tpl.name}|${tpl.language || 'pt_BR'}`;
                        opt.innerText = `${tpl.label || tpl.name} (${tpl.name})`;
                        selectEl.appendChild(opt);
                    });
                }
                // Adicionar opção para digitar manualmente
                const customOpt = document.createElement("option");
                customOpt.value = "__custom__";
                customOpt.innerText = "-- Digitar nome do template manualmente --";
                selectEl.appendChild(customOpt);

                if (currentSelection && [...selectEl.options].some(o => o.value === currentSelection)) {
                    selectEl.value = currentSelection;
                }
            }
        } catch (e) {
            console.error("Erro ao carregar templates:", e);
        }
    },

    async syncMetaTemplates() {
        const btn = document.getElementById("btn-sync-meta-templates");
        const origHtml = btn ? btn.innerHTML : "";
        if (btn) {
            btn.disabled = true;
            btn.innerHTML = `<i class="fa-solid fa-spinner fa-spin"></i> Sincronizando...`;
        }
        try {
            const res = await api.post("/api/inbox/templates/sync-meta");
            showToast(res.message || "Templates sincronizados com a Meta!", "success");
            await this.loadTemplates();
        } catch (err) {
            showToast("Erro na sincronização Meta: " + err.message, "error");
        } finally {
            if (btn) {
                btn.disabled = false;
                btn.innerHTML = origHtml;
            }
        }
    },

    async loadBillingSummary() {
        if (state.user && !["administrator", "manager", "superadmin"].includes(state.user.role)) {
            return;
        }
        const planNameEl = document.getElementById("billing-plan-name");
        const modeEl = document.getElementById("billing-active-mode");
        const balanceEl = document.getElementById("billing-balance");
        const spendEl = document.getElementById("billing-monthly-spend");
        const limitEl = document.getElementById("billing-postpaid-limit");
        const selectModeEl = document.getElementById("select-billing-mode");
        const rechargeSection = document.getElementById("prepaid-recharge-section");

        try {
            const summary = await api.get("/api/billing/summary");
            if (summary) {
                if (planNameEl) planNameEl.innerText = summary.plan_name || "Pro";
                if (modeEl) {
                    modeEl.innerText = "PRÉ-PAGO";
                    modeEl.style.color = "var(--color-success)";
                }
                if (balanceEl) balanceEl.innerText = `R$ ${(summary.balance || 0).toFixed(2)}`;
                if (spendEl) spendEl.innerText = `R$ ${(summary.monthly_spend || 0).toFixed(2)}`;
                if (limitEl) limitEl.innerText = `R$ ${(summary.postpaid_limit || 0).toFixed(2)}`;
                if (selectModeEl) selectModeEl.value = "prepaid";

                if (rechargeSection) {
                    rechargeSection.style.display = "flex";
                }
            }

            // Carrega transações
            const txsList = document.getElementById("billing-transactions-list");
            if (txsList) {
                txsList.innerHTML = "<tr><td colspan='4' style='padding:12px; text-align:center;'><i class='fa-solid fa-spinner fa-spin'></i> Carregando...</td></tr>";
                const txs = await api.get("/api/billing/transactions");
                txsList.innerHTML = "";
                if (!txs || txs.length === 0) {
                    txsList.innerHTML = "<tr><td colspan='4' style='padding:20px; text-align:center; opacity:0.5;'>Nenhum lançamento financeiro registrado.</td></tr>";
                } else {
                    txs.forEach(t => {
                        const tr = document.createElement("tr");
                        const date = new Date(t.created_at).toLocaleString("pt-BR");
                        
                        let categoryText = "Serviço";
                        if (t.category === "marketing") categoryText = "Marketing";
                        if (t.category === "utility") categoryText = "Utilidade";
                        if (t.category === "recharge") categoryText = "Recarga / Entrada";

                        const amountColor = t.category === "recharge" ? "color: var(--color-success);" : "color: var(--text-primary);";
                        const amountPrefix = t.category === "recharge" ? "+" : "-";

                        tr.innerHTML = `
                            <td>${date}</td>
                            <td><span class="badge" style="font-weight: 700;">${categoryText}</span></td>
                            <td>${t.description || "-"}</td>
                            <td style="${amountColor} font-weight:700;">${amountPrefix} R$ ${(t.amount || 0).toFixed(2)}</td>
                        `;
                        txsList.appendChild(tr);
                    });
                }
            }

        } catch (e) {
            console.error("Erro ao carregar faturamento:", e);
            if (planNameEl) planNameEl.innerText = "Pro";
            if (modeEl) modeEl.innerText = "PRÉ-PAGO";
        }
    },

    async openTransferModal() {
        const modal = document.getElementById("modal-transfer-chat");
        const listContainer = document.getElementById("transfer-users-list");
        if (!modal || !listContainer) return;

        modal.style.display = "flex";
        listContainer.innerHTML = "<p style='text-align: center; opacity: 0.6; padding: 12px;'><i class='fa-solid fa-spinner fa-spin'></i> Carregando equipe...</p>";

        try {
            const users = await api.get("/api/auth/users");
            listContainer.innerHTML = "";
            
            const currentUserId = state.user ? state.user.id : "";
            const availableUsers = users.filter(u => u.id !== currentUserId);

            if (availableUsers.length === 0) {
                listContainer.innerHTML = "<p style='text-align: center; opacity: 0.6; padding: 12px;'>Nenhum outro atendente cadastrado no momento.</p>";
                return;
            }

            availableUsers.forEach(u => {
                const userCard = document.createElement("div");
                userCard.className = "transfer-user-card";
                userCard.style.cssText = "display: flex; align-items: center; justify-content: space-between; padding: 10px 14px; border-radius: 8px; background: var(--bg-tertiary); border: 1px solid var(--border-color); cursor: pointer; transition: all 0.2s ease;";
                userCard.onmouseover = () => userCard.style.borderColor = "var(--color-brand)";
                userCard.onmouseout = () => userCard.style.borderColor = "var(--border-color)";
                
                const roleLabel = u.role === "administrator" ? "Administrador" : u.role === "manager" ? "Gerente" : "Atendente";
                const avatarSeed = u.name || u.email;
                
                userCard.innerHTML = `
                    <div style="display: flex; align-items: center; gap: 10px;">
                        <img class="avatar" src="https://api.dicebear.com/7.x/initials/svg?seed=${encodeURIComponent(avatarSeed)}" style="width: 32px; height: 32px; border-radius: 50%;">
                        <div>
                            <div style="font-size: 13px; font-weight: 600; color: var(--text-primary);">${u.name || u.email}</div>
                            <div style="font-size: 11px; opacity: 0.6;">${roleLabel} • ${u.email}</div>
                        </div>
                    </div>
                    <button class="btn btn-primary btn-sm" style="padding: 5px 10px; font-size: 12px;">Transferir</button>
                `;

                userCard.onclick = async () => {
                    try {
                        await api.post(`/api/inbox/conversations/${state.activeConversationId}/assign`, { user_id: u.id });
                        showToast(`Conversa transferida para ${u.name || u.email} com sucesso!`, "success");
                        this.closeTransferModal();

                        document.getElementById("active-chat-area").classList.add("empty");
                        document.getElementById("active-chat-area").querySelector(".no-chat-selected").style.display = "flex";
                        document.getElementById("active-chat-area").querySelector(".chat-wrapper").style.display = "none";
                        document.getElementById("guest-context").style.display = "none";

                        const activeTab = document.querySelector(".inbox-tabs .tab-btn.active");
                        const currentStatus = activeTab ? activeTab.getAttribute("data-status") : "waiting";
                        await this.loadConversations(currentStatus);
                    } catch (err) {
                        showToast("Erro ao transferir: " + err.message, "error");
                    }
                };

                listContainer.appendChild(userCard);
            });
        } catch (err) {
            listContainer.innerHTML = `<p style='color: var(--color-danger); text-align: center; padding: 12px;'>Erro ao carregar equipe: ${err.message}</p>`;
        }
    },

    closeTransferModal() {
        const modal = document.getElementById("modal-transfer-chat");
        if (modal) modal.style.display = "none";
    },

    startBackgroundSync() {
        if (!state.token || !state.tenant_id) return;
        
        // Garante a conexão WebSocket ativa em tempo real
        this.connectWebSocket();

        if (state.syncInterval) clearInterval(state.syncInterval);
        // Polling contínuo e silencioso a cada 3.5s (garantia de 100% que mensagens novas apareçam na tela sem F5)
        state.syncInterval = setInterval(async () => {
            if (!state.token || !state.tenant_id) return;
            try {
                // 1. Se o WebSocket caiu ou desconectou, tenta reconectar
                const isWsConnected = state.ws && state.ws.readyState === WebSocket.OPEN;
                if (!isWsConnected) {
                    this.connectWebSocket();
                }
                // 2. Atualiza silenciosamente a conversa aberta em tempo real
                if (state.activeConversationId) {
                    await this.refreshActiveMessagesSilent();
                }
                // 3. Atualiza silenciosamente a lista lateral de conversas
                await this.loadConversations(null, true);
            } catch (e) {}
        }, 3500);
    },

    async refreshActiveMessagesSilent() {
        if (!state.activeConversationId) return;
        try {
            const messages = await api.get(`/api/inbox/conversations/${state.activeConversationId}/messages`);
            const scroll = document.getElementById("message-scroll");
            if (!scroll) return;

            let addedAny = false;
            let lastSenderType = null;

            messages.forEach(m => {
                const existing = m.id && scroll.querySelector(`[data-msg-id="${m.id}"]`);
                if (!existing) {
                    const bubble = renderMessageBubble(m);
                    scroll.appendChild(bubble);
                    addedAny = true;
                    lastSenderType = m.sender_type;
                }
            });

            if (addedAny) {
                scroll.scrollTop = scroll.scrollHeight;
                if (lastSenderType === "contact") {
                    playNewMessageSound();
                }
            }
        } catch (e) {}
    },

    connectWebSocket(retryDelay = 1000) {
        if (!state.token || !state.tenant_id) return;

        updateWsStatusBadge("connecting");

        if (state.wsReconnectTimer) {
            clearTimeout(state.wsReconnectTimer);
            state.wsReconnectTimer = null;
        }

        if (state.ws) {
            try {
                state.ws.onopen = null;
                state.ws.onmessage = null;
                state.ws.onerror = null;
                state.ws.onclose = null;
                state.ws.close();
            } catch (e) {}
            state.ws = null;
        }
        if (state.pingInterval) clearInterval(state.pingInterval);

        const wsProtocol = window.location.protocol === "https:" ? "wss:" : "ws:";
        const wsHost = window.location.port === "3000" ? "localhost:8000" : window.location.host;
        state.ws = new WebSocket(`${wsProtocol}//${wsHost}/ws/${state.tenant_id}`);
        
        state.ws.onopen = () => {
            console.log("[WS] Conectado em tempo real!");
            updateWsStatusBadge("connected");
            if (state.wsReconnectTimer) {
                clearTimeout(state.wsReconnectTimer);
                state.wsReconnectTimer = null;
            }
            // Heartbeat Ping a cada 10 segundos para manter conexao viva sem queda de socket
            state.pingInterval = setInterval(() => {
                if (state.ws && state.ws.readyState === WebSocket.OPEN) {
                    state.ws.send("ping");
                }
            }, 10000);
        };
        
        state.ws.onmessage = (event) => {
            try {
                if (!event.data || event.data === "pong") return;
                const msg = typeof event.data === "string" ? JSON.parse(event.data) : event.data;
                if (!msg) return;

                // Eventos de transferência/atribuição/resolução
                if (msg.type === "conversation_transferred" || msg.type === "conversation_assigned" || msg.type === "conversation_resolved") {
                    this.loadConversations(null, true);
                    return;
                }

                // Tocar som e alerta para mensagem do contato
                if (msg.sender_type === "contact") {
                    playNewMessageSound();
                    showDesktopNotification(
                        msg.contact_name || "Novo Atendimento",
                        msg.body || "Mensagem recebida no WhatsApp",
                        msg.contact_avatar
                    );
                }

                // Atualizar conversa no estado
                const convo = (state.conversations || []).find(c => String(c.id) === String(msg.conversation_id));
                if (convo && msg.sender_type === "contact") {
                    convo.has_active_window = true;
                }

                // --- Atualizar bolha no chat aberto em tempo real (0ms) ---
                if (state.activeConversationId && String(msg.conversation_id || '').toLowerCase() === String(state.activeConversationId || '').toLowerCase()) {
                    if (msg.sender_type === "contact") {
                        const chatInputForm = document.getElementById("chat-input-form");
                        const chatBlockedArea = document.getElementById("chat-blocked-area");
                        if (chatInputForm && chatBlockedArea) {
                            chatInputForm.style.display = "flex";
                            chatBlockedArea.style.display = "none";
                        }
                    }

                    const scroll = document.getElementById("message-scroll");
                    if (scroll) {
                        const alreadyExists = msg.id && scroll.querySelector(`[data-msg-id="${msg.id}"]`);
                        if (!alreadyExists) {
                            const bubble = renderMessageBubble(msg);
                            scroll.appendChild(bubble);
                            scroll.scrollTop = scroll.scrollHeight;
                        }
                    }
                }

                // --- Atualização incremental da lista lateral ---
                this._updateConversationInList(msg);

            } catch (e) {
                console.error("[WS] Erro ao processar mensagem:", e);
            }
        };
        
        state.ws.onerror = (err) => {
            console.error("[WS] Erro:", err);
            updateWsStatusBadge("disconnected");
        };
        
        state.ws.onclose = () => {
            updateWsStatusBadge("disconnected");
            if (state.token && state.tenant_id) {
                console.log(`[WS] Desconectado. Reconectando em ${retryDelay}ms...`);
                if (state.wsReconnectTimer) clearTimeout(state.wsReconnectTimer);
                state.wsReconnectTimer = setTimeout(() => {
                    this.connectWebSocket(Math.min(retryDelay * 2, 30000));
                }, retryDelay);
            }
        };
    },

    initInactivityAndVisibilityManager() {
        if (this._inactivityManagerInitialized) return;
        this._inactivityManagerInitialized = true;

        const FIVE_MINUTES_MS = 5 * 60 * 1000; // 300.000 ms = 5 minutos
        let idleTimer = null;
        let hiddenTimer = null;
        let hiddenStartTime = null;

        const resetIdleTimer = () => {
            if (!state.token) return;
            if (idleTimer) clearTimeout(idleTimer);
            idleTimer = setTimeout(() => {
                if (state.token) {
                    console.warn("[Inactivity] 5 minutos de inatividade sem interação detectados. Efetuando logout...");
                    showToast("Sessão encerrada por inatividade (5 minutos sem uso).", "error");
                    this.logout(true);
                }
            }, FIVE_MINUTES_MS);
        };

        state.clearIdleTimer = () => {
            if (idleTimer) clearTimeout(idleTimer);
            if (hiddenTimer) clearTimeout(hiddenTimer);
        };

        // Eventos de atividade do usuário
        const activityEvents = ["mousemove", "keydown", "mousedown", "touchstart", "scroll"];
        activityEvents.forEach(evt => {
            window.addEventListener(evt, () => {
                resetIdleTimer();
            }, { passive: true });
        });

        // Monitoramento de aba em segundo plano (Visibility Change)
        document.addEventListener("visibilitychange", () => {
            if (!state.token) return;

            if (document.visibilityState === "hidden") {
                hiddenStartTime = Date.now();
                if (hiddenTimer) clearTimeout(hiddenTimer);
                hiddenTimer = setTimeout(() => {
                    if (state.token) {
                        console.warn("[Background] Aba em segundo plano por mais de 5 minutos. Efetuando logout...");
                        this.logout(true);
                    }
                }, FIVE_MINUTES_MS);
            } else if (document.visibilityState === "visible") {
                if (hiddenTimer) clearTimeout(hiddenTimer);

                if (hiddenStartTime) {
                    const elapsed = Date.now() - hiddenStartTime;
                    hiddenStartTime = null;
                    if (elapsed >= FIVE_MINUTES_MS) {
                        console.warn("[Background] Retornou após >5min em segundo plano. Deslogando...");
                        showToast("Sessão encerrada por inatividade em segundo plano.", "error");
                        this.logout(true);
                        return;
                    }
                }

                console.log("[Visibility] Aba visível novamente. Reconectando WebSocket e atualizando mensagens...");
                if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
                    this.connectWebSocket();
                }
                if (state.activeConversationId) {
                    this.refreshActiveMessagesSilent();
                }
                resetIdleTimer();
            }
        });

        resetIdleTimer();
    },

    // Atualiza apenas o item da conversa na lista sem recarregar tudo
    _updateConversationInList(msg) {
        const listContainer = document.getElementById("convo-list");
        if (!listContainer) return;

        const convoId = msg.conversation_id;
        let item = listContainer.querySelector(`[data-id="${convoId}"]`);
        const isActive = convoId === state.activeConversationId;
        const isIncoming = msg.sender_type === "contact";

        // Atualizar estado em memória
        const convo = (state.conversations || []).find(c => c.id === convoId);
        if (convo) {
            convo.last_message_sender_type = msg.sender_type;
            convo.last_message_at = msg.last_message_at || msg.created_at || new Date().toISOString();
            if (isIncoming) convo.has_active_window = true;
        }

        if (!item) {
            // Conversa não está na lista atual — recarregar a lista
            this.loadConversations();
            return;
        }

        // Atualizar visualização do destaque de pendência (.needs-response)
        const nameSpan = item.querySelector("h4 span");
        if (isIncoming) {
            item.classList.add("needs-response");
            if (nameSpan && !nameSpan.querySelector(".awaiting-badge")) {
                const awaitingBadge = document.createElement("span");
                awaitingBadge.className = "awaiting-badge";
                awaitingBadge.title = "Cliente aguardando resposta";
                awaitingBadge.innerHTML = `<i class="fa-solid fa-clock"></i> Pendente`;
                const nameNode = nameSpan.querySelector("span");
                if (nameNode && nameNode.nextSibling) {
                    nameSpan.insertBefore(awaitingBadge, nameNode.nextSibling);
                } else {
                    nameSpan.appendChild(awaitingBadge);
                }
            }
        } else {
            item.classList.remove("needs-response");
            if (nameSpan) {
                const awaitingBadge = nameSpan.querySelector(".awaiting-badge");
                if (awaitingBadge) awaitingBadge.remove();
            }
        }

        // Atualizar preview
        const previewEl = item.querySelector("p");
        if (previewEl) {
            let rawText = msg.body || msg.preview || '';
            if (rawText === "[Unsupported]" || rawText.toLowerCase().includes("unsupported")) {
                rawText = "ℹ️ [Mensagem de Sistema / Auth]";
            }
            const prefix = msg.sender_type === 'bot' ? '🤖 ' : msg.sender_type === 'agent' ? '✍️ ' : '';
            const previewText = prefix + rawText.substring(0, 50);
            previewEl.textContent = previewText;
            previewEl.style.cssText = "overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 12px; opacity: 0.7;";
        }

        // Atualizar horário
        const timeEl = item.querySelector(".convo-time");
        if (timeEl) timeEl.textContent = formatRelativeTime(msg.last_message_at || msg.created_at);

        // Atualizar badge de não lido (somente mensagens do contato e não é a conversa ativa)
        if (isIncoming && !isActive) {
            item.classList.add("unread");
            if (convo) {
                convo.unread_count = (convo.unread_count || 0) + 1;
                convo.unread = true;
            }
            let badge = item.querySelector(".unread-badge");
            const newCount = convo ? convo.unread_count : 1;
            if (!badge) {
                badge = document.createElement("span");
                badge.className = "unread-badge";
                badge.style.cssText = "background-color: var(--color-primary); color: white; border-radius: 50%; font-size: 10px; font-weight: 700; min-width: 18px; height: 18px; padding: 0 4px; display: inline-flex; align-items: center; justify-content: center; margin-left: 8px; box-shadow: 0 0 4px var(--color-primary);";
                if (nameSpan) nameSpan.appendChild(badge);
            }
            badge.textContent = newCount;
        }

        // Mover conversa para o topo da lista
        listContainer.removeChild(item);
        listContainer.insertBefore(item, listContainer.firstChild);
    }
};

// --- Form Submissions ---

// Login Submit
document.getElementById("login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const email = document.getElementById("login-email").value;
    const password = document.getElementById("login-password").value;
    
    try {
        const tokenRes = await api.post("/api/auth/login", { email, password }, false);
        state.token = tokenRes.access_token;
        localStorage.setItem("qa_token", state.token);

        // Busca perfil real do usuário
        const userProfile = await api.get("/api/auth/me");
        state.user = userProfile;
        state.tenant_id = userProfile.tenant_id;
        
        localStorage.setItem("qa_user", JSON.stringify(state.user));
        localStorage.setItem("qa_tenant_id", state.tenant_id);

        appRouter.showMainLayout();
        appRouter.init();
    } catch (err) {
        let msg = err.message || "Login ou senha incorretos, tente novamente.";
        if (msg.includes("Invalid Credentials") || msg.includes("Invalid credentials") || msg.includes("Falha no login")) {
            msg = "Login ou senha incorretos, tente novamente.";
        }
        showToast(msg, "error");
    }
});

// Onboarding Submit
document.getElementById("onboard-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const name = document.getElementById("onboard-name").value;
    const subdomain = document.getElementById("onboard-subdomain").value;
    
    try {
        const tenant = await api.post("/api/auth/onboard", { name, subdomain }, false);
        showToast(`Hotel ${tenant.name} criado com sucesso! Faça seu cadastro agora.`, "success");
        
        // Save tenant reference and prompt signup
        state.tenant_id = tenant.id;
        appRouter.navigate("login");
    } catch (err) {
        showToast("Erro no onboarding: " + err.message, "error");
    }
});

// Settings Meta Submit
document.getElementById("settings-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const phone_number_id = document.getElementById("phone-number-id").value;
    const waba_id = document.getElementById("waba-id").value;
    const verify_token = document.getElementById("verify-token").value;
    const permanent_access_token = document.getElementById("permanent-token").value;
    const n8n_webhook_url = document.getElementById("n8n-webhook-url").value;

    try {
        // Save Meta credentials
        await api.post("/api/auth/meta-credentials", {
            phone_number_id,
            waba_id,
            verify_token,
            permanent_access_token
        });

        // Save n8n webhook URL
        await api.post("/api/inbox/bot-config", {
            n8n_webhook_url
        });

        showToast("Configurações salvas com sucesso!", "success");
        appRouter.loadMetaSettings();
    } catch (err) {
        showToast("Erro ao salvar: " + err.message, "error");
    }
});

// --- Gerenciamento de Anexos no Chat (Upload e Ctrl+V Paste) ---
state.pendingAttachment = null;

function setPendingAttachment(file) {
    if (!file) return;
    state.pendingAttachment = file;
    
    const previewBar = document.getElementById("chat-attachment-preview-bar");
    const thumbnailEl = document.getElementById("chat-attachment-thumbnail");
    const filenameEl = document.getElementById("chat-attachment-filename");
    const filesizeEl = document.getElementById("chat-attachment-filesize");
    
    if (filenameEl) filenameEl.textContent = file.name;
    if (filesizeEl) {
        const kb = (file.size / 1024).toFixed(1);
        const mb = (file.size / (1024 * 1024)).toFixed(1);
        filesizeEl.textContent = file.size > 1024 * 1024 ? `${mb} MB` : `${kb} KB`;
    }
    
    if (thumbnailEl) {
        thumbnailEl.innerHTML = "";
        if (file.type.startsWith("image/")) {
            const reader = new FileReader();
            reader.onload = (e) => {
                const img = document.createElement("img");
                img.src = e.target.result;
                img.style.width = "100%";
                img.style.height = "100%";
                img.style.objectFit = "cover";
                thumbnailEl.appendChild(img);
            };
            reader.readAsDataURL(file);
        } else if (file.type.startsWith("video/")) {
            thumbnailEl.innerHTML = `<i class="fa-solid fa-file-video" style="font-size: 20px; color: #a855f7;"></i>`;
        } else if (file.type.startsWith("audio/")) {
            thumbnailEl.innerHTML = `<i class="fa-solid fa-file-audio" style="font-size: 20px; color: #10b981;"></i>`;
        } else if (file.type === "application/pdf") {
            thumbnailEl.innerHTML = `<i class="fa-solid fa-file-pdf" style="font-size: 20px; color: #ef4444;"></i>`;
        } else {
            thumbnailEl.innerHTML = `<i class="fa-solid fa-file-lines" style="font-size: 20px; color: #3b82f6;"></i>`;
        }
    }
    
    if (previewBar) previewBar.style.display = "flex";
    
    const input = document.getElementById("chat-message-input");
    if (input) input.focus();
}

function clearPendingAttachment() {
    state.pendingAttachment = null;
    const previewBar = document.getElementById("chat-attachment-preview-bar");
    if (previewBar) previewBar.style.display = "none";
    const fileInput = document.getElementById("chat-file-input");
    if (fileInput) fileInput.value = "";
}

// Event Bindings para Anexo e Paste
document.addEventListener("DOMContentLoaded", () => {
    const attachBtn = document.getElementById("chat-attach-btn");
    const fileInput = document.getElementById("chat-file-input");
    const removeAttachBtn = document.getElementById("chat-attachment-remove-btn");
    
    if (attachBtn && fileInput) {
        attachBtn.addEventListener("click", () => fileInput.click());
        fileInput.addEventListener("change", (e) => {
            if (e.target.files && e.target.files.length > 0) {
                setPendingAttachment(e.target.files[0]);
            }
        });
    }
    
    if (removeAttachBtn) {
        removeAttachBtn.addEventListener("click", clearPendingAttachment);
    }
    
    // Suporte a COLAR (Ctrl+V) de imagens e arquivos no chat
    const chatInputForm = document.getElementById("chat-input-form");
    const chatTextarea = document.getElementById("chat-message-input");
    
    const handlePasteEvent = (e) => {
        const clipboardData = e.clipboardData || (e.originalEvent && e.originalEvent.clipboardData);
        if (!clipboardData || !clipboardData.items) return;
        
        const items = clipboardData.items;
        for (let i = 0; i < items.length; i++) {
            const item = items[i];
            if (item.kind === "file") {
                const blob = item.getAsFile();
                if (blob) {
                    e.preventDefault();
                    let fileToAttach = blob;
                    if (item.type.startsWith("image/") && (!blob.name || blob.name === "image.png")) {
                        const ext = item.type.split("/")[1] || "png";
                        fileToAttach = new File([blob], `imagem_colada_${Date.now()}.${ext}`, { type: item.type });
                    }
                    setPendingAttachment(fileToAttach);
                    if (typeof showToast === "function") {
                        showToast("Imagem/Arquivo colado do clipboard!", "info");
                    }
                    break;
                }
            }
        }
    };
    
    if (chatTextarea) chatTextarea.addEventListener("paste", handlePasteEvent);
    if (chatInputForm) chatInputForm.addEventListener("paste", handlePasteEvent);

    // Drag & drop
    if (chatInputForm) {
        chatInputForm.addEventListener("dragover", (e) => {
            e.preventDefault();
            chatInputForm.style.borderColor = "var(--color-brand, #3b82f6)";
        });
        chatInputForm.addEventListener("dragleave", (e) => {
            e.preventDefault();
            chatInputForm.style.borderColor = "";
        });
        chatInputForm.addEventListener("drop", (e) => {
            e.preventDefault();
            chatInputForm.style.borderColor = "";
            if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length > 0) {
                setPendingAttachment(e.dataTransfer.files[0]);
                if (typeof showToast === "function") {
                    showToast("Arquivo arrastado para o chat!", "info");
                }
            }
        });
    }
});

// Captura a tecla Enter no campo de texto para enviar a mensagem (Shift+Enter para nova linha)
const chatTextarea = document.getElementById("chat-message-input");
if (chatTextarea) {
    chatTextarea.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            document.getElementById("chat-input-form").dispatchEvent(new Event("submit", { cancelable: true, bubbles: true }));
        }
    });
}

// Chat Send Submit (com suporte a Mídia / Arquivos / Imagens e Renderização Otimista Instantânea em 0ms)
document.getElementById("chat-input-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const input = document.getElementById("chat-message-input");
    const body = input.value.trim();
    
    if ((!body && !state.pendingAttachment) || !state.activeConversationId) return;

    const attachmentFile = state.pendingAttachment;
    input.value = "";
    clearPendingAttachment();

    const scroll = document.getElementById("message-scroll");
    const tempId = `temp_${Date.now()}`;
    const userDisplayName = (state.user && state.user.name) ? state.user.name : "Pedro";

    // CASO 1: Envio de Mídia / Arquivo / Imagem
    if (attachmentFile) {
        let mediaType = "document";
        if (attachmentFile.type.startsWith("image/")) mediaType = "image";
        else if (attachmentFile.type.startsWith("video/")) mediaType = "video";
        else if (attachmentFile.type.startsWith("audio/")) mediaType = "audio";

        const tempBlobUrl = URL.createObjectURL(attachmentFile);
        const formattedBody = body ? `*Atendente ${userDisplayName}:* ${body}` : (mediaType === "document" ? `*Atendente ${userDisplayName}:* ${attachmentFile.name}` : `*Atendente ${userDisplayName}:* [${mediaType.charAt(0).toUpperCase() + mediaType.slice(1)}]`);

        const tempMsgObj = {
            id: tempId,
            conversation_id: state.activeConversationId,
            sender_type: "agent",
            body: formattedBody,
            message_type: mediaType,
            media_url: tempBlobUrl,
            media_mime_type: attachmentFile.type,
            created_at: new Date().toISOString()
        };

        let tempBubble = null;
        if (scroll) {
            if (scroll.innerText.includes("Nenhuma mensagem ainda")) scroll.innerHTML = "";
            tempBubble = renderMessageBubble(tempMsgObj);
            tempBubble.style.opacity = "0.75";
            scroll.appendChild(tempBubble);
            scroll.scrollTop = scroll.scrollHeight;
        }

        appRouter._updateConversationInList({
            ...tempMsgObj,
            preview: `📎 ${attachmentFile.name}`
        });

        try {
            const formData = new FormData();
            formData.append("conversation_id", state.activeConversationId);
            if (body) formData.append("caption", body);
            formData.append("file", attachmentFile);

            const newMsg = await api.postForm("/api/inbox/send-media", formData);
            if (newMsg && newMsg.id) {
                if (tempBubble) {
                    tempBubble.setAttribute("data-msg-id", newMsg.id);
                    tempBubble.style.opacity = "1";
                    if (newMsg.media_url) {
                        const mediaElem = tempBubble.querySelector("img, video, audio, a");
                        if (mediaElem) {
                            if (mediaElem.tagName === "A") mediaElem.href = getMediaSrc(newMsg.media_url);
                            else mediaElem.src = getMediaSrc(newMsg.media_url);
                        }
                    }
                }
            }
        } catch (err) {
            if (tempBubble) tempBubble.remove();
            input.value = body;
            showToast("Erro ao enviar arquivo: " + err.message, "error");
        }
        return;
    }

    // CASO 2: Envio de Texto Puro (Mensagem WhatsApp ou Nota Interna Privada)
    const isInternalNote = state.chatMode === "note";
    const formattedBody = isInternalNote ? body : `*Atendente ${userDisplayName}:* ${body}`;
    const tempMsgObj = {
        id: tempId,
        conversation_id: state.activeConversationId,
        sender_type: "agent",
        body: formattedBody,
        message_type: "text",
        internal_note: isInternalNote,
        created_at: new Date().toISOString()
    };

    let tempBubble = null;
    if (scroll) {
        if (scroll.innerText.includes("Nenhuma mensagem ainda")) {
            scroll.innerHTML = "";
        }
        tempBubble = renderMessageBubble(tempMsgObj);
        tempBubble.style.opacity = "0.75";
        scroll.appendChild(tempBubble);
        scroll.scrollTop = scroll.scrollHeight;
    }

    if (!isInternalNote) {
        appRouter._updateConversationInList(tempMsgObj);
    }

    try {
        const newMsg = await api.post("/api/inbox/send-message", {
            conversation_id: state.activeConversationId,
            body: body,
            internal_note: isInternalNote
        });

        if (newMsg && newMsg.id) {
            if (tempBubble) {
                tempBubble.setAttribute("data-msg-id", newMsg.id);
                tempBubble.style.opacity = "1";
            }
        }
    } catch (err) {
        if (tempBubble) tempBubble.remove();
        input.value = body;
        showToast("Erro ao enviar: " + err.message, "error");
    }
});

// --- Inbox Tab Filter Click Bindings ---
document.querySelectorAll(".inbox-tabs .tab-btn").forEach(btn => {
    btn.addEventListener("click", async (e) => {
        document.querySelectorAll(".inbox-tabs .tab-btn").forEach(b => b.classList.remove("active"));
        e.currentTarget.classList.add("active");
        const status = e.currentTarget.getAttribute("data-status");
        // Carrega em modo silencioso para troca instantânea de abas sem piscar "Carregando..."
        await appRouter.loadConversations(status, true);
    });
});

// --- Chat Workspace Actions (Transfer / Resolve) ---
document.getElementById("btn-transfer-chat").addEventListener("click", async () => {
    if (!state.activeConversationId) return;
    
    // Identifica se a conversa atual está na fila (aguardando ou no bot)
    const convo = state.conversations.find(c => c.id === state.activeConversationId);
    const isWaiting = convo && (convo.status === "waiting" || convo.status === "bot");
    
    if (isWaiting) {
        try {
            await api.post(`/api/inbox/conversations/${state.activeConversationId}/assign`, {});
            showToast("Atendimento assumido! Iniciando conversa...", "success");
            
            document.querySelectorAll(".inbox-tabs .tab-btn").forEach(b => {
                if (b.getAttribute("data-status") === "active") {
                    b.classList.add("active");
                } else {
                    b.classList.remove("active");
                }
            });
            
            await appRouter.loadConversations("active");
            await appRouter.selectConversation(state.activeConversationId);
        } catch (err) {
            showToast("Erro ao assumir atendimento: " + err.message, "error");
        }
    } else {
        // Se já estiver ativa, abre o modal de transferência de equipe
        appRouter.openTransferModal();
    }
});

document.getElementById("btn-resolve-chat").addEventListener("click", async () => {
    if (!state.activeConversationId) return;
    try {
        await api.post(`/api/inbox/conversations/${state.activeConversationId}/resolve`, {});
        showToast("Conversa resolvida!", "success");
        
        // Limpa tela do chat
        document.getElementById("active-chat-area").classList.add("empty");
        document.getElementById("active-chat-area").querySelector(".no-chat-selected").style.display = "flex";
        document.getElementById("active-chat-area").querySelector(".chat-wrapper").style.display = "none";
        document.getElementById("guest-context").style.display = "none";

        const activeTab = document.querySelector(".inbox-tabs .tab-btn.active");
        const currentStatus = activeTab ? activeTab.getAttribute("data-status") : "waiting";
        await appRouter.loadConversations(currentStatus);
    } catch (err) {
        showToast("Erro ao resolver conversa: " + err.message, "error");
    }
});


// --- Context Panel Actions (Name Edit & Hide/Show Ficha) ---

// Save Client Name (Ficha do Hóspede)
document.getElementById("btn-save-guest-name").addEventListener("click", async () => {
    const activeConvo = state.conversations.find(c => c.id === state.activeConversationId);
    if (!activeConvo || !activeConvo.contact) return;
    
    const nameInput = document.getElementById("guest-name-input");
    const newName = nameInput.value.trim();
    if (!newName) {
        showToast("O nome do cliente não pode estar vazio.", "error");
        return;
    }
    
    try {
        const updatedContact = await api.put(`/api/inbox/contacts/${activeConvo.contact.id}`, { name: newName });
        
        // Update state name
        activeConvo.contact.name = updatedContact.name;
        
        // Update header UI
        document.getElementById("active-contact-name").innerText = updatedContact.name;
        
        // Reload conversations list to show new name in queue
        const activeTab = document.querySelector(".inbox-tabs .tab-btn.active");
        const currentStatus = activeTab ? activeTab.getAttribute("data-status") : "waiting";
        await appRouter.loadConversations(currentStatus);
        
        showToast("Nome do cliente atualizado com sucesso!", "success");
    } catch (e) {
        console.error(e);
        showToast("Erro ao salvar nome do cliente.", "error");
    }
});

// Hide Context Panel (Ficha)
document.getElementById("btn-hide-context").addEventListener("click", () => {
    state.isContextPanelVisible = false;
    document.getElementById("guest-context").style.display = "none";
    
    const layout = document.querySelector(".inbox-layout");
    if (layout) layout.classList.add("hide-context");
    
    const showContextBtn = document.getElementById("btn-show-context");
    if (showContextBtn) showContextBtn.style.display = "block";
});

// Show Context Panel (Ficha)
document.getElementById("btn-show-context").addEventListener("click", () => {
    state.isContextPanelVisible = true;
    document.getElementById("guest-context").style.display = "block";
    
    const layout = document.querySelector(".inbox-layout");
    if (layout) layout.classList.remove("hide-context");
    
    const showContextBtn = document.getElementById("btn-show-context");
    if (showContextBtn) showContextBtn.style.display = "none";
});

// Transfer Conversation back to Bot
document.getElementById("btn-transfer-to-bot").addEventListener("click", async () => {
    if (!state.activeConversationId) return;
    
    try {
        await api.post(`/api/inbox/conversations/${state.activeConversationId}/transfer-to-bot`, {});
        showToast("Conversa enviada para o Bot com sucesso!", "success");
        
        // Reload conversations queue
        const activeTab = document.querySelector(".inbox-tabs .tab-btn.active");
        const currentStatus = activeTab ? activeTab.getAttribute("data-status") : "waiting";
        await appRouter.loadConversations(currentStatus);
        
        // Clear active chat workspace since it went back to the bot queue
        document.getElementById("active-chat-area").classList.add("empty");
        document.getElementById("active-chat-area").querySelector(".no-chat-selected").style.display = "flex";
        document.getElementById("active-chat-area").querySelector(".chat-wrapper").style.display = "none";
        document.getElementById("guest-context").style.display = "none";
    } catch (err) {
        showToast("Erro ao transferir para o Bot: " + err.message, "error");
    }
});

// --- Client Flagging (Flegar Cliente) Action ---
const flagToggleBtn = document.getElementById("chat-flag-toggle");
const flagSelectorMenu = document.getElementById("flag-selector-menu");

if (flagToggleBtn && flagSelectorMenu) {
    // Show/hide menu on flag click
    flagToggleBtn.addEventListener("click", (e) => {
        e.stopPropagation();
        const isHidden = flagSelectorMenu.style.display === "none";
        flagSelectorMenu.style.display = isHidden ? "flex" : "none";
    });

    // Close menu when clicking anywhere else
    document.addEventListener("click", () => {
        flagSelectorMenu.style.display = "none";
    });

    // Handle flag options clicks
    flagSelectorMenu.querySelectorAll("i").forEach(opt => {
        opt.addEventListener("click", async (e) => {
            e.stopPropagation();
            flagSelectorMenu.style.display = "none";

            if (!state.activeConversationId) return;

            const selectedFlag = opt.getAttribute("data-flag");

            try {
                const updatedConvo = await api.post(`/api/inbox/conversations/${state.activeConversationId}/set-flag?flag_type=${selectedFlag}`);
                
                // Update state cache
                const cachedConvo = state.conversations.find(c => c.id === state.activeConversationId);
                if (cachedConvo) {
                    cachedConvo.is_flagged = updatedConvo.is_flagged;
                    cachedConvo.flag_type = updatedConvo.flag_type;
                }

                // Update active chat header flag color
                let headerColor = "var(--text-muted)";
                if (updatedConvo.flag_type === "red") headerColor = "#ef4444";
                else if (updatedConvo.flag_type === "yellow") headerColor = "#fbbf24";
                else if (updatedConvo.flag_type === "blue") headerColor = "#3b82f6";
                else if (updatedConvo.flag_type === "green") headerColor = "#10b981";

                flagToggleBtn.style.color = headerColor;
                flagToggleBtn.title = updatedConvo.flag_type !== "none" ? `Flag: ${updatedConvo.flag_type.toUpperCase()}` : "Flegar Cliente";

                showToast(updatedConvo.flag_type !== "none" ? `Cliente flegado com sucesso!` : "Flag removida!", "success");

                // Update conversation list item
                const convoItem = document.querySelector(`.convo-item[data-id="${state.activeConversationId}"]`);
                if (convoItem) {
                    const nameSpan = convoItem.querySelector("h4 span");
                    if (nameSpan) {
                        const oldFlag = nameSpan.querySelector(".fa-flag");
                        if (oldFlag) oldFlag.remove();

                        if (updatedConvo.flag_type !== "none") {
                            const flagEl = document.createElement("i");
                            flagEl.className = "fa-solid fa-flag";
                            flagEl.style.color = headerColor;
                            flagEl.style.marginLeft = "6px";
                            flagEl.style.fontSize = "11px";
                            flagEl.title = `Flag: ${updatedConvo.flag_type.toUpperCase()}`;
                            nameSpan.appendChild(flagEl);
                        }
                    }
                }

            } catch (err) {
                showToast("Erro ao flegar cliente: " + err.message, "error");
            }
        });
    });
}

// --- AUDIO RECORDING MANAGER ---
let mediaRecorder = null;
let audioChunks = [];
let recordingStartTime = null;
let recordingTimerInterval = null;

async function startAudioRecording() {
    if (!state.activeConversationId) {
        showToast("Selecione uma conversa para gravar áudio.", "warning");
        return;
    }

    try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        
        let mimeType = "audio/webm;codecs=opus";
        if (!MediaRecorder.isTypeSupported(mimeType)) {
            if (MediaRecorder.isTypeSupported("audio/ogg;codecs=opus")) {
                mimeType = "audio/ogg;codecs=opus";
            } else if (MediaRecorder.isTypeSupported("audio/mp4")) {
                mimeType = "audio/mp4";
            } else {
                mimeType = "";
            }
        }

        mediaRecorder = mimeType ? new MediaRecorder(stream, { mimeType }) : new MediaRecorder(stream);
        audioChunks = [];

        mediaRecorder.ondataavailable = (e) => {
            if (e.data && e.data.size > 0) {
                audioChunks.push(e.data);
            }
        };

        mediaRecorder.onstop = () => {
            stream.getTracks().forEach(track => track.stop());
        };

        mediaRecorder.start(100);
        recordingStartTime = Date.now();

        // Switch UI to recording bar
        document.getElementById("chat-input-form").style.display = "none";
        const recordingBar = document.getElementById("chat-recording-bar");
        recordingBar.style.display = "flex";
        
        const timerSpan = document.getElementById("recording-timer");
        timerSpan.innerText = "00:00";

        clearInterval(recordingTimerInterval);
        recordingTimerInterval = setInterval(() => {
            const elapsedSeconds = Math.floor((Date.now() - recordingStartTime) / 1000);
            const mins = String(Math.floor(elapsedSeconds / 60)).padStart(2, "0");
            const secs = String(elapsedSeconds % 60).padStart(2, "0");
            timerSpan.innerText = `${mins}:${secs}`;
        }, 500);

    } catch (err) {
        console.error("Erro ao acessar microfone:", err);
        showToast("Não foi possível acessar o microfone. Verifique as permissões do seu navegador.", "error");
    }
}

function cancelAudioRecording() {
    if (mediaRecorder && mediaRecorder.state !== "inactive") {
        mediaRecorder.stop();
    }
    clearInterval(recordingTimerInterval);
    audioChunks = [];
    const recBar = document.getElementById("chat-recording-bar");
    if (recBar) recBar.style.display = "none";
    const inputForm = document.getElementById("chat-input-form");
    if (inputForm) inputForm.style.display = "flex";
}

async function sendRecordedAudio() {
    if (!mediaRecorder) return;

    clearInterval(recordingTimerInterval);

    const stopped = new Promise(resolve => {
        mediaRecorder.onstop = () => {
            resolve();
        };
    });

    if (mediaRecorder.state !== "inactive") {
        mediaRecorder.stop();
    }
    await stopped;

    if (audioChunks.length === 0) {
        cancelAudioRecording();
        return;
    }

    const mimeType = mediaRecorder.mimeType || "audio/ogg";
    const extension = mimeType.includes("mp4") ? "mp4" : (mimeType.includes("webm") ? "webm" : "ogg");
    const audioBlob = new Blob(audioChunks, { type: mimeType });
    const audioFile = new File([audioBlob], `audio_${Date.now()}.${extension}`, { type: mimeType });

    // Reset UI
    const recBar = document.getElementById("chat-recording-bar");
    if (recBar) recBar.style.display = "none";
    const inputForm = document.getElementById("chat-input-form");
    if (inputForm) inputForm.style.display = "flex";

    // Send via send-media
    const scroll = document.getElementById("message-scroll");
    const tempId = `temp_${Date.now()}`;
    const userDisplayName = (state.user && state.user.name) ? state.user.name : "Pedro";
    const tempBlobUrl = URL.createObjectURL(audioBlob);

    const tempMsgObj = {
        id: tempId,
        conversation_id: state.activeConversationId,
        sender_type: "agent",
        body: `*Atendente ${userDisplayName}:* [Áudio]`,
        message_type: "audio",
        media_url: tempBlobUrl,
        media_mime_type: mimeType,
        created_at: new Date().toISOString()
    };

    let tempBubble = null;
    if (scroll) {
        if (scroll.innerText.includes("Nenhuma mensagem ainda")) scroll.innerHTML = "";
        tempBubble = renderMessageBubble(tempMsgObj);
        tempBubble.style.opacity = "0.75";
        scroll.appendChild(tempBubble);
        scroll.scrollTop = scroll.scrollHeight;
    }

    appRouter._updateConversationInList({
        ...tempMsgObj,
        preview: `🎤 Áudio gravado`
    });

    try {
        const formData = new FormData();
        formData.append("conversation_id", state.activeConversationId);
        formData.append("file", audioFile);

        const newMsg = await api.postForm("/api/inbox/send-media", formData);
        if (newMsg && newMsg.id) {
            if (tempBubble) {
                tempBubble.setAttribute("data-msg-id", newMsg.id);
                tempBubble.style.opacity = "1";
                if (newMsg.media_url) {
                    const audioElem = tempBubble.querySelector("audio");
                    if (audioElem) audioElem.src = getMediaSrc(newMsg.media_url);
                }
            }
        }
        showToast("Áudio enviado com sucesso!", "success");
    } catch (err) {
        if (tempBubble) tempBubble.remove();
        showToast("Erro ao enviar áudio: " + (err.message || err), "error");
    }
}

// Audio Recording Event Listeners
const chatMicBtn = document.getElementById("chat-mic-btn");
if (chatMicBtn) {
    chatMicBtn.addEventListener("click", startAudioRecording);
}

const btnCancelRecording = document.getElementById("btn-cancel-recording");
if (btnCancelRecording) {
    btnCancelRecording.addEventListener("click", cancelAudioRecording);
}

const btnSendRecording = document.getElementById("btn-send-recording");
if (btnSendRecording) {
    btnSendRecording.addEventListener("click", sendRecordedAudio);
}

// Template Preview Modal Event Listeners
const btnCloseTemplatePreview = document.getElementById("btn-close-template-preview-modal");
if (btnCloseTemplatePreview) {
    btnCloseTemplatePreview.addEventListener("click", () => appRouter.closeTemplatePreviewModal());
}

const btnCancelTemplatePreview = document.getElementById("btn-cancel-template-preview");
if (btnCancelTemplatePreview) {
    btnCancelTemplatePreview.addEventListener("click", () => appRouter.closeTemplatePreviewModal());
}

const btnConfirmSendTemplate = document.getElementById("btn-confirm-send-template");
if (btnConfirmSendTemplate) {
    btnConfirmSendTemplate.addEventListener("click", () => appRouter.confirmSendTemplate());
}

// --- CRM & Marketing Campaign Handler ---
tempContacts = [];

const dragDropArea = document.getElementById("contacts-drag-drop");
const fileInput = document.getElementById("contacts-file-input");

if (dragDropArea && fileInput) {
    dragDropArea.addEventListener("click", () => fileInput.click());

    dragDropArea.addEventListener("dragover", (e) => {
        e.preventDefault();
        dragDropArea.style.borderColor = "var(--color-primary)";
        dragDropArea.style.background = "rgba(79, 70, 229, 0.05)";
    });

    dragDropArea.addEventListener("dragleave", () => {
        dragDropArea.style.borderColor = "var(--border-color)";
        dragDropArea.style.background = "var(--bg-primary)";
    });

    dragDropArea.addEventListener("drop", (e) => {
        e.preventDefault();
        dragDropArea.style.borderColor = "var(--border-color)";
        dragDropArea.style.background = "var(--bg-primary)";
        if (e.dataTransfer.files.length > 0) {
            handleContactsFile(e.dataTransfer.files[0]);
        }
    });

    fileInput.addEventListener("change", (e) => {
        if (e.target.files.length > 0) {
            handleContactsFile(e.target.files[0]);
        }
    });
}

function handleContactsFile(file) {
    if (!file || !file.name) return;
    const name = file.name.toLowerCase();
    if (!name.endsWith(".csv") && !name.endsWith(".xlsx") && !name.endsWith(".xls")) {
        showToast("Por favor, envie um arquivo Excel (.xlsx, .xls) ou CSV.", "error");
        return;
    }
    
    if (name.endsWith(".xlsx") || name.endsWith(".xls")) {
        try {
            const reader = new FileReader();
            reader.onload = function(e) {
                try {
                    const data = new Uint8Array(e.target.result);
                    const workbook = XLSX.read(data, { type: 'array' });
                    const firstSheetName = workbook.SheetNames[0];
                    const worksheet = workbook.Sheets[firstSheetName];
                    const csv = XLSX.utils.sheet_to_csv(worksheet);
                    parseCSVContacts(csv);
                } catch (err) {
                    console.error("Erro ao ler Excel:", err);
                    showToast("Erro ao processar planilha Excel: " + err.message, "error");
                }
            };
            reader.readAsArrayBuffer(file);
        } catch (e) {
            console.error("Erro no leitor de Excel:", e);
            showToast("Falha ao abrir o arquivo Excel.", "error");
        }
    } else {
        const reader = new FileReader();
        reader.onload = function(e) {
            const text = e.target.result;
            parseCSVContacts(text);
        };
        reader.readAsText(file, "UTF-8");
    }
}

// Download template Excel/CSV
const downloadCsvTemplateBtn = document.getElementById("download-csv-template");
if (downloadCsvTemplateBtn) {
    downloadCsvTemplateBtn.addEventListener("click", (e) => {
        e.preventDefault();
        const csvContent = "Nome,Telefone\nJoão Silva,5562993473656\nMaria Santos,5511987654321\n";
        const blob = new Blob(["\uFEFF" + csvContent], { type: "text/csv;charset=utf-8;" });
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.setAttribute("href", url);
        link.setAttribute("download", "modelo_lista_contatos.csv");
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
    });
}

function parseCSVContacts(text) {
    if (!text) return;
    // Remove marcador BOM UTF-8 se presente
    text = text.replace(/^\uFEFF/, "");
    const lines = text.split(/\r?\n/);
    if (lines.length <= 1) {
        showToast("O arquivo CSV está vazio ou não possui contatos.", "error");
        return;
    }
    
    tempContacts = [];
    const firstLine = lines[0].trim();
    const delimiter = firstLine.includes(";") ? ";" : ",";
    const headers = firstLine.split(delimiter).map(h => h.trim().toLowerCase().replace(/^["']|["']$/g, ""));
    
    let nameIdx = headers.findIndex(h => h.includes("nome") || h.includes("name"));
    let phoneIdx = headers.findIndex(h => h.includes("telefone") || h.includes("fone") || h.includes("phone") || h.includes("celular") || h.includes("whatsapp"));
    
    if (nameIdx === -1) nameIdx = 0;
    if (phoneIdx === -1) phoneIdx = headers.length > 1 ? 1 : 0;
    
    for (let i = 1; i < lines.length; i++) {
        const line = lines[i].trim();
        if (!line) continue;
        
        const cols = line.split(delimiter).map(c => c.trim().replace(/^["']|["']$/g, ""));
        if (cols.length === 0) continue;
        
        const name = cols[nameIdx] || "Hóspede";
        const phone = cols[phoneIdx] || cols[0] || "";
        
        const digitsOnly = phone.replace(/\D/g, "");
        if (digitsOnly && digitsOnly.length >= 8) {
            tempContacts.push({ name: name.trim() || "Hóspede", phone_number: phone.trim() });
        }
    }
    
    if (tempContacts.length === 0) {
        showToast("Nenhum número de telefone válido foi detectado na planilha CSV.", "error");
        return;
    }

    updateContactsPreview();
    showToast(`${tempContacts.length} contatos detectados na planilha!`, "success");
}

function updateContactsPreview() {
    const tbody = document.querySelector("#contacts-preview-table tbody");
    const countEl = document.getElementById("contacts-count");
    const saveBtn = document.getElementById("btn-save-contacts");
    
    if (!tbody || !countEl || !saveBtn) return;
    
    tbody.innerHTML = "";
    countEl.innerText = tempContacts.length;
    
    if (tempContacts.length === 0) {
        tbody.innerHTML = `<tr><td colspan="2" style="text-align: center; padding: 20px; opacity: 0.5;">Nenhum contato carregado.</td></tr>`;
        saveBtn.disabled = true;
        saveBtn.classList.remove("btn-primary");
        saveBtn.classList.add("btn-secondary");
        return;
    }
    
    // Exibir prévia dos primeiros 20
    const previewList = tempContacts.slice(0, 20);
    previewList.forEach(c => {
        const tr = document.createElement("tr");
        tr.innerHTML = `
            <td style="padding: 6px; border-bottom: 1px solid var(--border-color);">${c.name}</td>
            <td style="padding: 6px; border-bottom: 1px solid var(--border-color);">${c.phone_number}</td>
        `;
        tbody.appendChild(tr);
    });
    
    if (tempContacts.length > 20) {
        const tr = document.createElement("tr");
        tr.innerHTML = `
            <td colspan="2" style="text-align: center; padding: 6px; opacity: 0.6; font-style: italic;">...e mais ${tempContacts.length - 20} contatos.</td>
        `;
        tbody.appendChild(tr);
    }
    
    saveBtn.disabled = false;
    saveBtn.classList.remove("btn-secondary");
    saveBtn.classList.add("btn-primary");
}

// Bulk Save
const saveContactsBtn = document.getElementById("btn-save-contacts");
if (saveContactsBtn) {
    saveContactsBtn.addEventListener("click", async () => {
        if (tempContacts.length === 0) return;
        
        saveContactsBtn.disabled = true;
        saveContactsBtn.innerText = "Salvando contatos...";
        
        try {
            const res = await api.post("/api/inbox/contacts/bulk", { contacts: tempContacts });
            showToast(`${res.imported} contatos importados com sucesso!`, "success");
            
            // Limpa o cache
            tempContacts = [];
            updateContactsPreview();
        } catch (err) {
            showToast("Erro ao importar contatos: " + err.message, "error");
        } finally {
            saveContactsBtn.disabled = false;
            saveContactsBtn.innerText = "Salvar Contatos no CRM";
        }
    });
}

// --- Live Preview Synchronizer ---
function syncCampaignPreview() {
    const useTemplateCheck = document.getElementById("campaign-use-template");
    const previewBody = document.getElementById("preview-message-body");
    const headerMedia = document.getElementById("preview-header-media");
    const previewImg = document.getElementById("preview-header-img");
    const previewVideo = document.getElementById("preview-header-video");
    const previewAudio = document.getElementById("preview-audio-media");
    const previewBtn = document.getElementById("preview-action-button");
    const mediaFileGroup = document.getElementById("campaign-media-file-group");

    if (useTemplateCheck && useTemplateCheck.checked) {
        const mediaTypeSelect = document.getElementById("campaign-media-type");
        const mediaType = mediaTypeSelect ? mediaTypeSelect.value : "none";
        
        if (mediaFileGroup) {
            if (mediaType !== "none") {
                mediaFileGroup.style.display = "block";
            } else {
                mediaFileGroup.style.display = "none";
                document.getElementById("campaign-media-url").value = "";
                const fileInputEl = document.getElementById("campaign-media-file");
                if (fileInputEl) fileInputEl.value = "";
            }
        }
        
        headerMedia.style.display = "none";
        previewImg.style.display = "none";
        previewVideo.style.display = "none";
        previewAudio.style.display = "none";
        previewBtn.style.display = "none";
        
        // Mockup preview with media header if uploaded
        const mediaUrl = document.getElementById("campaign-media-url").value.trim();
        if (mediaType !== "none" && mediaUrl) {
            headerMedia.style.display = "block";
            if (mediaType === "image") {
                previewImg.style.display = "block";
                previewImg.src = mediaUrl;
            } else if (mediaType === "video") {
                previewVideo.style.display = "block";
                previewVideo.src = mediaUrl;
            }
        }
        
        const templateNameVal = document.getElementById("campaign-template-name").value.trim() || "nome_do_modelo";
        const templateLangVal = document.getElementById("campaign-template-lang").value.trim() || "pt_BR";
        
        previewBody.innerHTML = `<span style="color:var(--color-brand); font-weight:700;"><i class="fa-solid fa-square-poll-horizontal"></i> Modelo Aprovado (Template)</span>\n\nTemplate: <strong>${templateNameVal}</strong>\nIdioma: <strong>${templateLangVal}</strong>\n\n<span style="font-size: 10px; opacity: 0.7;">*(O texto final é determinado pelo modelo cadastrado na Meta)*</span>`;
        return;
    }

    const mediaTypeSelect = document.getElementById("campaign-media-type");
    if (!mediaTypeSelect) return; // Prevent run on separate views
    
    const mediaType = mediaTypeSelect.value;
    const mediaUrl = document.getElementById("campaign-media-url").value.trim();
    const body = document.getElementById("campaign-body").value;
    const buttonType = document.getElementById("campaign-button-type").value;
    const btnLabel = document.getElementById("campaign-btn-label").value.trim();
    const btnUrl = document.getElementById("campaign-btn-url").value.trim();
    
    // Exibe ou oculta campo de arquivo de mídia
    if (mediaFileGroup) {
        if (mediaType !== "none") {
            mediaFileGroup.style.display = "block";
        } else {
            mediaFileGroup.style.display = "none";
            // Limpa o link de mídia e o input de arquivo caso alterado para nenhum
            document.getElementById("campaign-media-url").value = "";
            const fileInputEl = document.getElementById("campaign-media-file");
            if (fileInputEl) fileInputEl.value = "";
        }
    }
    
    // Elementos do mockup de celular
    headerMedia.style.display = "none";
    previewImg.style.display = "none";
    previewVideo.style.display = "none";
    previewAudio.style.display = "none";
    
    if (mediaType === "image") {
        headerMedia.style.display = "block";
        previewImg.style.display = "block";
        previewImg.src = mediaUrl || "https://images.unsplash.com/photo-1566073771259-6a8506099945?w=400";
    } else if (mediaType === "video") {
        headerMedia.style.display = "block";
        previewVideo.style.display = "block";
    } else if (mediaType === "audio") {
        previewAudio.style.display = "flex";
    }
    
    // Texto do corpo
    previewBody.innerHTML = formatMessageBody(body || "Olá! Temos uma novidade incrível para você...");
    
    // Controles de Botões
    const btnFields = document.getElementById("campaign-button-fields");
    const btnUrlGroup = document.getElementById("campaign-btn-url-group");
    
    if (buttonType !== "none") {
        btnFields.style.display = "block";
        if (buttonType === "cta_url") {
            btnUrlGroup.style.display = "block";
        } else {
            btnUrlGroup.style.display = "none";
        }
    } else {
        btnFields.style.display = "none";
    }
    
    // Visualização do Botão
    const previewBtnAnchor = document.getElementById("preview-btn-anchor");
    const previewBtnText = document.getElementById("preview-btn-text");
    const previewBtnIcon = document.getElementById("preview-btn-icon");
    
    if (buttonType !== "none" && btnLabel) {
        previewBtn.style.display = "block";
        previewBtnText.innerText = btnLabel;
        if (buttonType === "cta_url") {
            previewBtnAnchor.href = btnUrl || "#";
            previewBtnIcon.style.display = "inline-block";
        } else {
            previewBtnAnchor.href = "#";
            previewBtnIcon.style.display = "none";
        }
    } else {
        previewBtn.style.display = "none";
    }
}

// Vincula ouvintes
const useTemplateCheck = document.getElementById("campaign-use-template");
if (useTemplateCheck) {
    useTemplateCheck.addEventListener("change", () => {
        const customFields = document.getElementById("campaign-custom-fields");
        const templateFields = document.getElementById("campaign-template-fields");
        
        if (useTemplateCheck.checked) {
            customFields.style.display = "none";
            templateFields.style.display = "flex";
        } else {
            customFields.style.display = "flex";
            templateFields.style.display = "none";
        }
        syncCampaignPreview();
    });

    document.getElementById("campaign-template-name").addEventListener("input", syncCampaignPreview);
    document.getElementById("campaign-template-lang").addEventListener("input", syncCampaignPreview);
}

const mediaTypeField = document.getElementById("campaign-media-type");
if (mediaTypeField) {
    mediaTypeField.addEventListener("change", syncCampaignPreview);
    document.getElementById("campaign-body").addEventListener("input", syncCampaignPreview);
    document.getElementById("campaign-button-type").addEventListener("change", syncCampaignPreview);
    document.getElementById("campaign-btn-label").addEventListener("input", syncCampaignPreview);
    document.getElementById("campaign-btn-url").addEventListener("input", syncCampaignPreview);
    
    // Ouvinte para upload automático do arquivo de campanha
    const campaignFileInput = document.getElementById("campaign-media-file");
    if (campaignFileInput) {
        campaignFileInput.addEventListener("change", async (e) => {
            const file = e.target.files[0];
            if (!file) return;
            
            showToast("Enviando arquivo de mídia...", "success");
            
            const formData = new FormData();
            formData.append("file", file);
            
            try {
                const response = await fetch(`${API_URL}/api/inbox/upload-media`, {
                    method: "POST",
                    headers: {
                        "Authorization": `Bearer ${state.token}`
                    },
                    body: formData
                });
                
                if (!response.ok) {
                    const errData = await response.json();
                    throw new Error(errData.detail || "Erro no servidor ao salvar arquivo");
                }
                
                const res = await response.json();
                // Gera a URL absoluta para a API da Meta poder baixar a imagem pública
                const absoluteUrl = `${API_URL}${res.url}`;
                
                document.getElementById("campaign-media-url").value = absoluteUrl;
                showToast("Arquivo de mídia enviado com sucesso!", "success");
                
                // Atualiza o mockup de telefone
                syncCampaignPreview();
            } catch (err) {
                showToast("Erro no envio: " + err.message, "error");
                campaignFileInput.value = "";
                document.getElementById("campaign-media-url").value = "";
                syncCampaignPreview();
            }
        });
    }
}

// Enviar Campanha
const dispatchCampaignBtn = document.getElementById("btn-dispatch-campaign");
if (dispatchCampaignBtn) {
    dispatchCampaignBtn.addEventListener("click", async () => {
        const name = document.getElementById("campaign-name").value.trim();
        const useTemplate = document.getElementById("campaign-use-template").checked;
        
        let payload = {};

        if (!name) {
            showToast("Por favor, informe o nome da campanha.", "error");
            return;
        }

        if (useTemplate) {
            const templateName = document.getElementById("campaign-template-name").value.trim();
            const templateLang = document.getElementById("campaign-template-lang").value.trim();
            
            if (!templateName) {
                showToast("Por favor, insira o nome do modelo aprovado na Meta.", "error");
                return;
            }

            const mediaType = document.getElementById("campaign-media-type").value;
            const mediaUrl = document.getElementById("campaign-media-url").value.trim();

            payload = {
                name,
                use_template: true,
                template_name: templateName,
                template_language: templateLang || "pt_BR",
                media_type: mediaType,
                media_url: mediaType !== "none" ? mediaUrl : null,
                body: `[Template: ${templateName}]`,
                button_type: "none"
            };
        } else {
            const mediaType = document.getElementById("campaign-media-type").value;
            const mediaUrl = document.getElementById("campaign-media-url").value.trim();
            const body = document.getElementById("campaign-body").value.trim();
            const buttonType = document.getElementById("campaign-button-type").value;
            const btnLabel = document.getElementById("campaign-btn-label").value.trim();
            const btnUrl = document.getElementById("campaign-btn-url").value.trim();

            if (!body) {
                showToast("Por favor, escreva a mensagem da campanha.", "error");
                return;
            }
            if (buttonType !== "none" && !btnLabel) {
                showToast("Por favor, informe o texto do botão.", "error");
                return;
            }
            if (buttonType === "cta_url" && !btnUrl) {
                showToast("Por favor, insira a URL do link do botão.", "error");
                return;
            }

            payload = {
                name,
                use_template: false,
                media_type: mediaType,
                media_url: mediaType !== "none" ? mediaUrl : null,
                body,
                button_type: buttonType,
                button_label: buttonType !== "none" ? btnLabel : null,
                button_url: buttonType === "cta_url" ? btnUrl : null
            };
        }
        
        dispatchCampaignBtn.disabled = true;
        
        // Se o usuário selecionou uma planilha de contatos mas esqueceu de clicar em Salvar, salva automaticamente!
        if (typeof tempContacts !== "undefined" && tempContacts.length > 0) {
            try {
                dispatchCampaignBtn.innerText = "Salvando contatos da lista...";
                await api.post("/api/inbox/contacts/bulk", { contacts: tempContacts });
                tempContacts = [];
                if (typeof updateContactsPreview === "function") updateContactsPreview();
            } catch (saveErr) {
                showToast("Erro ao salvar a lista de contatos: " + saveErr.message, "error");
                dispatchCampaignBtn.disabled = false;
                dispatchCampaignBtn.innerText = "Disparar Campanha para Lista";
                return;
            }
        }

        dispatchCampaignBtn.innerText = "Agendando disparos...";
        
        try {
            await api.post("/api/inbox/campaigns/send", payload);
            
            showToast("Disparo de campanha iniciado em segundo plano com sucesso!", "success");
            
            // Limpa o formulário
            document.getElementById("campaign-name").value = "";
            document.getElementById("campaign-use-template").checked = false;
            document.getElementById("campaign-template-name").value = "";
            document.getElementById("campaign-template-lang").value = "pt_BR";
            
            document.getElementById("campaign-custom-fields").style.display = "flex";
            document.getElementById("campaign-template-fields").style.display = "none";
            
            document.getElementById("campaign-media-type").value = "none";
            document.getElementById("campaign-media-url").value = "";
            const fileInputEl = document.getElementById("campaign-media-file");
            if (fileInputEl) fileInputEl.value = "";
            document.getElementById("campaign-body").value = "";
            document.getElementById("campaign-button-type").value = "none";
            document.getElementById("campaign-btn-label").value = "";
            document.getElementById("campaign-btn-url").value = "";
            syncCampaignPreview();
        } catch (err) {
            showToast("Erro ao disparar campanha: " + err.message, "error");
        } finally {
            dispatchCampaignBtn.disabled = false;
            dispatchCampaignBtn.innerText = "Disparar Campanha para Lista";
        }
    });
}

// Chatbot Config Submit
const chatbotForm = document.getElementById("chatbot-config-form");
if (chatbotForm) {
    const welcomeMsgEl = document.getElementById("bot-welcome-msg");
    if (welcomeMsgEl) {
        welcomeMsgEl.addEventListener("input", (e) => {
            const preview = document.getElementById("preview-bot-welcome-text");
            if (preview) {
                preview.innerHTML = formatMessageBody(e.target.value || "Olá! Bem-vindo ao nosso hotel...");
            }
        });
    }

    chatbotForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        
        const is_active = document.getElementById("bot-active-toggle").checked;
        const welcome_message = document.getElementById("bot-welcome-msg").value.trim();
        const fallback_message = document.getElementById("bot-fallback-msg").value.trim();
        const out_of_hours_message = document.getElementById("bot-out-hours-msg").value.trim();
        const transfer_keywords = document.getElementById("bot-transfer-keywords").value.trim();
        
        const btn = document.getElementById("btn-save-bot-config");
        btn.disabled = true;
        btn.innerText = "Salvando...";
        
        try {
            await api.post("/api/inbox/bot-config", {
                is_active,
                welcome_message,
                fallback_message,
                out_of_hours_message: out_of_hours_message || null,
                transfer_keywords
            });
            showToast("Configurações do Chatbot salvas com sucesso!", "success");
        } catch (err) {
            showToast("Erro ao salvar chatbot: " + err.message, "error");
        } finally {
            btn.disabled = false;
            btn.innerText = "Salvar Configurações";
        }
    });
}

// Team Creation Submit
const teamForm = document.getElementById("team-create-form");
if (teamForm) {
    teamForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        
        const name = document.getElementById("team-name").value.trim();
        const email = document.getElementById("team-email").value.trim();
        const password = document.getElementById("team-password").value;
        const role = document.getElementById("team-role").value;
        
        if (password.length < 6) {
            showToast("A senha precisa ter pelo menos 6 caracteres.", "error");
            return;
        }
        
        const btn = document.getElementById("btn-save-team-user");
        btn.disabled = true;
        btn.innerText = "Cadastrando...";
        
        try {
            await api.post("/api/auth/users", {
                name,
                email,
                password,
                role
            });
            
            showToast("Colaborador cadastrado com sucesso!", "success");
            
            // Clean fields
            document.getElementById("team-name").value = "";
            document.getElementById("team-email").value = "";
            document.getElementById("team-password").value = "";
            
            // Reload table
            appRouter.loadTeamUsers();
        } catch (err) {
            showToast("Erro ao cadastrar colaborador: " + err.message, "error");
        } finally {
            btn.disabled = false;
            btn.innerText = "Cadastrar Usuário";
        }
    });
}

// --- Start Chat Modal Event Listeners & Form Submit ---
const startChatModal = document.getElementById("start-chat-modal");
const openStartChatModalBtn = document.getElementById("btn-open-start-chat-modal");
const closeStartChatModalBtn = document.getElementById("btn-close-start-chat-modal");
const startChatForm = document.getElementById("start-chat-form");

if (openStartChatModalBtn && startChatModal) {
    openStartChatModalBtn.addEventListener("click", () => {
        startChatModal.style.display = "flex";
        document.getElementById("start-chat-phone").focus();
        appRouter.loadTemplates();
    });
}

const startChatTemplateSelect = document.getElementById("start-chat-template-select");
const startChatManualContainer = document.getElementById("start-chat-manual-template-container");
const startChatTemplateLang = document.getElementById("start-chat-template-lang");

if (startChatTemplateSelect) {
    startChatTemplateSelect.addEventListener("change", (e) => {
        const val = e.target.value;
        if (val === "__custom__") {
            if (startChatManualContainer) startChatManualContainer.style.display = "flex";
            const customInput = document.getElementById("start-chat-custom-template-name");
            if (customInput) customInput.focus();
        } else {
            if (startChatManualContainer) startChatManualContainer.style.display = "none";
            const parts = val.split("|");
            if (parts.length > 1 && startChatTemplateLang) {
                startChatTemplateLang.value = parts[1];
            }
        }
    });
}

if (closeStartChatModalBtn && startChatModal) {
    closeStartChatModalBtn.addEventListener("click", () => {
        startChatModal.style.display = "none";
        startChatForm.reset();
        if (startChatManualContainer) startChatManualContainer.style.display = "none";
    });
}

if (startChatModal) {
    startChatModal.addEventListener("click", (e) => {
        if (e.target === startChatModal) {
            startChatModal.style.display = "none";
            startChatForm.reset();
            if (startChatManualContainer) startChatManualContainer.style.display = "none";
        }
    });
}

if (startChatForm) {
    startChatForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        
        const phone = document.getElementById("start-chat-phone").value.trim();
        const name = document.getElementById("start-chat-name").value.trim();
        const selectVal = startChatTemplateSelect ? startChatTemplateSelect.value : "";
        let templateName = "";
        let templateLang = startChatTemplateLang ? startChatTemplateLang.value.trim() : "pt_BR";

        if (selectVal === "__custom__") {
            const customInput = document.getElementById("start-chat-custom-template-name");
            templateName = customInput ? customInput.value.trim() : "";
            if (!templateName) {
                showToast("Por favor, informe o nome exato do template na Meta.", "error");
                return;
            }
        } else if (selectVal) {
            templateName = selectVal.split("|")[0];
        } else {
            templateName = "primeiro_contato";
        }
        
        const submitBtn = startChatForm.querySelector("button[type='submit']");
        const originalText = submitBtn.innerText;
        submitBtn.disabled = true;
        submitBtn.innerText = "Iniciando...";
        
        try {
            const messageRes = await api.post("/api/inbox/start-conversation", {
                phone_number: phone,
                name: name || null,
                template_name: templateName,
                template_language: templateLang
            });
            
            showToast("Conversa iniciada com sucesso via Template!", "success");
            startChatModal.style.display = "none";
            startChatForm.reset();
            if (startChatManualContainer) startChatManualContainer.style.display = "none";
            
            // Switch tab to "Minhas" (active) to show the new conversation
            const minhasTab = document.querySelector(".inbox-tabs button[data-status='active']");
            if (minhasTab) {
                document.querySelectorAll(".inbox-tabs .tab-btn").forEach(b => b.classList.remove("active"));
                minhasTab.classList.add("active");
            }
            
            // Reload conversations list and select the conversation
            await appRouter.loadConversations("active");
            
            if (messageRes && messageRes.conversation_id) {
                appRouter.selectConversation(messageRes.conversation_id);
            }
        } catch (err) {
            showToast("Erro ao iniciar conversa: " + err.message, "error");
        } finally {
            submitBtn.disabled = false;
            submitBtn.innerText = originalText;
        }
    });
}

// Quick Message Editor Form Submit
const quickForm = document.getElementById("quick-message-form");
if (quickForm) {
    quickForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const shortcut = document.getElementById("quick-shortcut").value.trim();
        const body = document.getElementById("quick-body").value.trim();
        const is_global = document.getElementById("quick-is-global").checked;

        const btn = quickForm.querySelector("button[type='submit']");
        btn.disabled = true;
        btn.innerText = "Salvando...";

        try {
            await api.post("/api/inbox/quick-messages", { shortcut, body, is_global });
            showToast("Resposta rápida cadastrada!", "success");
            document.getElementById("quick-shortcut").value = "";
            document.getElementById("quick-body").value = "";
            document.getElementById("quick-is-global").checked = false;
            appRouter.loadQuickMessages();
        } catch (err) {
            showToast("Erro ao salvar: " + err.message, "error");
        } finally {
            btn.disabled = false;
            btn.innerText = "Adicionar";
        }
    });
}

// Template Message Form Submit (Configurações)
const templateMsgForm = document.getElementById("template-message-form");
if (templateMsgForm) {
    templateMsgForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        const name = document.getElementById("template-name").value.trim();
        const label = document.getElementById("template-label").value.trim();
        const language = document.getElementById("template-language").value.trim() || "pt_BR";
        const category = document.getElementById("template-category").value;

        const btn = templateMsgForm.querySelector("button[type='submit']");
        const origHtml = btn ? btn.innerHTML : "";
        if (btn) btn.disabled = true;

        try {
            await api.post("/api/inbox/templates", {
                name: name,
                label: label || name,
                language: language,
                category: category
            });
            showToast("Template cadastrado com sucesso!", "success");
            templateMsgForm.reset();
            const langEl = document.getElementById("template-language");
            if (langEl) langEl.value = "pt_BR";
            await appRouter.loadTemplates();
        } catch (err) {
            showToast("Erro ao cadastrar template: " + err.message, "error");
        } finally {
            if (btn) {
                btn.disabled = false;
                btn.innerHTML = origHtml;
            }
        }
    });
}

// Autocomplete Dropdown Logic for Quick Replies (/)
const chatInput = document.getElementById("chat-message-input");
const qmDropdown = document.getElementById("quick-replies-dropdown");
let selectedQuickIndex = -1;
let filteredQuickReplies = [];

if (chatInput && qmDropdown) {
    chatInput.addEventListener("keydown", (e) => {
        if (qmDropdown.style.display === "flex") {
            if (e.key === "ArrowDown") {
                e.preventDefault();
                selectedQuickIndex = (selectedQuickIndex + 1) % filteredQuickReplies.length;
                updateDropdownSelection();
                return;
            } else if (e.key === "ArrowUp") {
                e.preventDefault();
                selectedQuickIndex = (selectedQuickIndex - 1 + filteredQuickReplies.length) % filteredQuickReplies.length;
                updateDropdownSelection();
                return;
            } else if (e.key === "Enter") {
                if (selectedQuickIndex >= 0 && selectedQuickIndex < filteredQuickReplies.length) {
                    e.preventDefault();
                    selectQuickReply(filteredQuickReplies[selectedQuickIndex]);
                    return;
                }
            } else if (e.key === "Escape") {
                qmDropdown.style.display = "none";
                return;
            }
        }

        // Send message on Enter (without Shift)
        if (e.key === "Enter" && !e.shiftKey) {
            e.preventDefault();
            const form = document.getElementById("chat-input-form");
            if (form) {
                form.dispatchEvent(new Event("submit"));
            }
        }
    });

    chatInput.addEventListener("input", (e) => {
        const val = e.target.value;
        const lastSlashIdx = val.lastIndexOf("/");
        
        if (lastSlashIdx !== -1 && (lastSlashIdx === val.length - 1 || val.substring(lastSlashIdx).indexOf(" ") === -1)) {
            const search = val.substring(lastSlashIdx + 1).toLowerCase();
            const list = state.quickMessages || [];
            filteredQuickReplies = list.filter(qm => qm.shortcut.toLowerCase().includes(search));
            
            if (filteredQuickReplies.length > 0) {
                renderDropdown(filteredQuickReplies);
            } else {
                qmDropdown.style.display = "none";
            }
        } else {
            qmDropdown.style.display = "none";
        }
    });

    document.addEventListener("click", (e) => {
        if (e.target !== chatInput && e.target !== qmDropdown && !qmDropdown.contains(e.target)) {
            qmDropdown.style.display = "none";
        }
    });

    // --- Emoji Picker setup ---
    const emojiBtn = document.getElementById("chat-emoji-btn");
    const emojiPopup = document.getElementById("emoji-picker-popup");
    const emojiGrid = document.getElementById("emoji-grid");

    if (emojiBtn && emojiPopup && emojiGrid) {
        const emojis = [
            "👋", "😊", "👍", "🙌", "👏", "🎉", "🔥", "❤️", "🤖", "💬", "📞", "📧", "📍", "📅", "⏰", "🛍️",
            "💰", "🎁", "💡", "❓", "❗", "✅", "❌", "⚠️", "🏠", "🔑", "🚗", "✈️", "🤩", "😍", "😅",
            "😂", "😉", "😎", "🚀", "✨", "🌟", "😱", "🥳"
        ];

        // Populate grid
        emojis.forEach(emoji => {
            const span = document.createElement("span");
            span.innerText = emoji;
            span.style.padding = "4px";
            span.style.borderRadius = "4px";
            span.style.transition = "background 0.2s";
            span.style.userSelect = "none";
            span.addEventListener("mouseover", () => span.style.background = "var(--bg-tertiary)");
            span.addEventListener("mouseout", () => span.style.background = "transparent");
            span.addEventListener("click", (e) => {
                e.stopPropagation();
                const startPos = chatInput.selectionStart;
                const endPos = chatInput.selectionEnd;
                const text = chatInput.value;
                chatInput.value = text.substring(0, startPos) + emoji + text.substring(endPos);
                chatInput.focus();
                
                const newPos = startPos + emoji.length;
                chatInput.setSelectionRange(newPos, newPos);
                
                emojiPopup.style.display = "none";
            });
            emojiGrid.appendChild(span);
        });

        // Toggle popup
        emojiBtn.addEventListener("click", (e) => {
            e.stopPropagation();
            const display = emojiPopup.style.display;
            emojiPopup.style.display = display === "block" ? "none" : "block";
        });

        // Close on click outside
        document.addEventListener("click", (e) => {
            if (!emojiPopup.contains(e.target) && e.target !== emojiBtn && !emojiBtn.contains(e.target)) {
                emojiPopup.style.display = "none";
            }
        });
    }
}

function renderDropdown(items) {
    qmDropdown.innerHTML = "";
    qmDropdown.style.display = "flex";
    selectedQuickIndex = 0;
    
    items.forEach((item, idx) => {
        const div = document.createElement("div");
        div.className = `quick-reply-item ${idx === 0 ? 'selected' : ''}`;
        div.innerHTML = `
            <span class="shortcut-badge">/${item.shortcut}</span>
            <span class="message-preview">${item.body}</span>
            <span class="scope-badge ${item.is_global ? 'global' : 'personal'}">${item.is_global ? 'global' : 'pessoal'}</span>
        `;
        div.onclick = () => selectQuickReply(item);
        qmDropdown.appendChild(div);
    });
}

function updateDropdownSelection() {
    const items = qmDropdown.querySelectorAll(".quick-reply-item");
    items.forEach((item, idx) => {
        if (idx === selectedQuickIndex) {
            item.classList.add("selected");
            item.scrollIntoView({ block: "nearest" });
        } else {
            item.classList.remove("selected");
        }
    });
}

function selectQuickReply(item) {
    const val = chatInput.value;
    const lastSlashIdx = val.lastIndexOf("/");
    if (lastSlashIdx !== -1) {
        chatInput.value = val.substring(0, lastSlashIdx) + item.body;
    } else {
        chatInput.value = item.body;
    }
    qmDropdown.style.display = "none";
    chatInput.focus();
}

// Download CSV Template click handler
const downloadCsvBtn = document.getElementById("download-csv-template");
if (downloadCsvBtn) {
    downloadCsvBtn.addEventListener("click", (e) => {
        e.preventDefault();
        const csvContent = "Nome,Telefone\nJoão da Silva,5511999999999\nMaria Souza,5511988888888\n";
        const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
        const url = URL.createObjectURL(blob);
        const link = document.createElement("a");
        link.setAttribute("href", url);
        link.setAttribute("download", "modelo_contatos.csv");
        link.style.visibility = "hidden";
        document.body.appendChild(link);
        link.click();
        document.body.removeChild(link);
    });
}



// --- CRM Campaign Reports Handler ---
appRouter.loadCampaigns = async function() {
    const listEl = document.getElementById("crm-campaigns-list");
    if (!listEl) return;

    try {
        const campaigns = await api.get("/api/inbox/campaigns");
        listEl.innerHTML = "";

        if (campaigns.length === 0) {
            listEl.innerHTML = `<tr><td colspan="2" style="text-align: center; padding: 20px; opacity: 0.5;">Nenhuma campanha enviada.</td></tr>`;
            this.updateCampaignsSummary([]);
            return;
        }

        campaigns.forEach(camp => {
            const tr = document.createElement("tr");
            tr.style.cursor = "pointer";
            tr.className = "campaign-row";
            tr.setAttribute("data-id", camp.id);
            
            const dateFormatted = new Date(camp.created_at).toLocaleDateString("pt-BR", {
                day: "2-digit",
                month: "2-digit",
                year: "numeric",
                hour: "2-digit",
                minute: "2-digit"
            });

            tr.innerHTML = `
                <td><strong>${camp.name}</strong></td>
                <td style="font-size: 11px; opacity: 0.8;">${dateFormatted}</td>
            `;
            
            tr.onclick = () => this.showCampaignDetail(camp);
            listEl.appendChild(tr);
        });

        this.updateCampaignsSummary(campaigns);

    } catch (err) {
        showToast("Erro ao carregar relatórios: " + err.message, "error");
    }
};

appRouter.updateCampaignsSummary = function(campaigns) {
    let totalSent = 0;
    let totalDelivered = 0;
    let totalRead = 0;
    let totalClicked = 0;

    campaigns.forEach(c => {
        totalSent += (c.sent_count || 0);
        totalDelivered += (c.delivered_count || 0);
        totalRead += (c.read_count || 0);
        totalClicked += (c.click_count || 0);
    });

    const deliveryRate = totalSent > 0 ? Math.round((totalDelivered / totalSent) * 100) : 0;
    const readRate = totalSent > 0 ? Math.round((totalRead / totalSent) * 100) : 0;
    const clickRate = totalSent > 0 ? Math.round((totalClicked / totalSent) * 100) : 0;

    document.getElementById("rep-total-sent").innerText = totalSent;
    document.getElementById("rep-total-delivered").innerText = `${deliveryRate}%`;
    document.getElementById("rep-progress-delivered").style.width = `${deliveryRate}%`;
    document.getElementById("rep-total-read").innerText = `${readRate}%`;
    document.getElementById("rep-progress-read").style.width = `${readRate}%`;
    document.getElementById("rep-total-clicked").innerText = `${clickRate}%`;
    document.getElementById("rep-progress-clicked").style.width = `${clickRate}%`;
};

appRouter.showCampaignDetail = function(camp) {
    const detailsPanel = document.getElementById("campaign-details-panel");
    if (!detailsPanel) return;

    // Highlight selected row
    document.querySelectorAll(".campaign-row").forEach(row => {
        row.style.background = "transparent";
        if (row.getAttribute("data-id") === camp.id) {
            row.style.background = "rgba(79, 70, 229, 0.1)";
        }
    });

    const dateFormatted = new Date(camp.created_at).toLocaleDateString("pt-BR", {
        day: "2-digit",
        month: "2-digit",
        year: "numeric",
        hour: "2-digit",
        minute: "2-digit"
    });

    const deliveryPercent = camp.sent_count > 0 ? Math.round((camp.delivered_count / camp.sent_count) * 100) : 0;
    const readPercent = camp.sent_count > 0 ? Math.round((camp.read_count / camp.sent_count) * 100) : 0;
    const clickPercent = camp.sent_count > 0 ? Math.round((camp.click_count / camp.sent_count) * 100) : 0;

    let mediaBadge = "";
    if (camp.media_type && camp.media_type !== "none") {
        mediaBadge = `<span class="scope-badge global" style="font-size:10px; margin-left: 6px;">Mídia: ${camp.media_type.toUpperCase()}</span>`;
    }

    detailsPanel.style.textAlign = "left";
    detailsPanel.style.justifyContent = "flex-start";
    detailsPanel.style.alignItems = "stretch";

    detailsPanel.innerHTML = `
        <div style="display:flex; justify-content:space-between; align-items:center; border-bottom:1px solid var(--border-color); padding-bottom:12px; margin-bottom:16px;">
            <h3 style="margin:0; font-size:16px; font-weight:700; color:var(--text-primary);">${camp.name} ${mediaBadge}</h3>
            <span style="font-size:11px; opacity:0.6;">Disparado em: ${dateFormatted}</span>
        </div>

        <div class="metrics-grid" style="display:grid; grid-template-columns:repeat(4, 1fr); gap:12px; margin-bottom:20px;">
            <div style="background:var(--bg-primary); border:1px solid var(--border-color); padding:12px; border-radius:var(--radius-sm); text-align:center;">
                <span style="font-size:10px; color:var(--text-muted);">Disparos</span>
                <h4 style="font-size:18px; font-weight:800; margin:4px 0 0 0;">${camp.sent_count}</h4>
            </div>
            <div style="background:var(--bg-primary); border:1px solid var(--border-color); padding:12px; border-radius:var(--radius-sm); text-align:center;">
                <span style="font-size:10px; color:var(--text-muted);">Entregues</span>
                <h4 style="font-size:18px; font-weight:800; margin:4px 0 0 0; color:var(--color-primary);">${camp.delivered_count} <span style="font-size:10px; font-weight:600; opacity:0.8;">(${deliveryPercent}%)</span></h4>
            </div>
            <div style="background:var(--bg-primary); border:1px solid var(--border-color); padding:12px; border-radius:var(--radius-sm); text-align:center;">
                <span style="font-size:10px; color:var(--text-muted);">Lidos</span>
                <h4 style="font-size:18px; font-weight:800; margin:4px 0 0 0; color:var(--color-success);">${camp.read_count} <span style="font-size:10px; font-weight:600; opacity:0.8;">(${readPercent}%)</span></h4>
            </div>
            <div style="background:var(--bg-primary); border:1px solid var(--border-color); padding:12px; border-radius:var(--radius-sm); text-align:center;">
                <span style="font-size:10px; color:var(--text-muted);">Cliques</span>
                <h4 style="font-size:18px; font-weight:800; margin:4px 0 0 0; color:var(--color-warning);">${camp.click_count} <span style="font-size:10px; font-weight:600; opacity:0.8;">(${clickPercent}%)</span></h4>
            </div>
        </div>

        <!-- Conversion Funnel Visualization -->
        <h4 style="font-size:12px; font-weight:700; margin-bottom:12px; text-transform:uppercase; letter-spacing:0.5px;">Funil de Conversão</h4>
        <div style="display:flex; flex-direction:column; gap:10px; background:var(--bg-primary); border:1px solid var(--border-color); padding:16px; border-radius:var(--radius-md); margin-bottom:20px;">
            <div>
                <div style="display:flex; justify-content:space-between; font-size:11px; margin-bottom:4px;"><span>1. Envios Iniciados</span><strong style="opacity:0.8;">${camp.sent_count} (100%)</strong></div>
                <div style="height:8px; background:var(--border-color); border-radius:4px; overflow:hidden;"><div style="width:100%; height:100%; background:var(--text-muted);"></div></div>
            </div>
            <div>
                <div style="display:flex; justify-content:space-between; font-size:11px; margin-bottom:4px;"><span>2. Recebidos (Entregues)</span><strong>${camp.delivered_count} (${deliveryPercent}%)</strong></div>
                <div style="height:8px; background:var(--border-color); border-radius:4px; overflow:hidden;"><div style="width:${deliveryPercent}%; height:100%; background:var(--color-primary); transition: width 0.4s;"></div></div>
            </div>
            <div>
                <div style="display:flex; justify-content:space-between; font-size:11px; margin-bottom:4px;"><span>3. Abertos (Lidos)</span><strong>${camp.read_count} (${readPercent}%)</strong></div>
                <div style="height:8px; background:var(--border-color); border-radius:4px; overflow:hidden;"><div style="width:${readPercent}%; height:100%; background:var(--color-success); transition: width 0.4s;"></div></div>
            </div>
            <div>
                <div style="display:flex; justify-content:space-between; font-size:11px; margin-bottom:4px;"><span>4. Clicados (Engajamento)</span><strong>${camp.click_count} (${clickPercent}%)</strong></div>
                <div style="height:8px; background:var(--border-color); border-radius:4px; overflow:hidden;"><div style="width:${clickPercent}%; height:100%; background:var(--color-warning); transition: width 0.4s;"></div></div>
            </div>
        </div>

        <h4 style="font-size:12px; font-weight:700; margin-bottom:8px; text-transform:uppercase; letter-spacing:0.5px;">Mensagem Enviada</h4>
        <div style="background:var(--bg-secondary); border:1px solid var(--border-color); padding:12px; border-radius:var(--radius-sm); font-size:11px; white-space:pre-wrap; max-height:120px; overflow-y:auto; color:var(--text-primary); font-family:var(--font-primary);">
            ${camp.body}
        </div>
    `;
};

// CRM Sub-tabs toggling click handlers
const crmTabSendBtn = document.getElementById("crm-tab-send");
const crmTabReportsBtn = document.getElementById("crm-tab-reports");
const crmSendPanel = document.getElementById("crm-send-panel");
const crmReportsPanel = document.getElementById("crm-reports-panel");

if (crmTabSendBtn && crmTabReportsBtn && crmSendPanel && crmReportsPanel) {
    crmTabSendBtn.addEventListener("click", () => {
        crmTabSendBtn.classList.add("active");
        crmTabReportsBtn.classList.remove("active");
        crmTabSendBtn.style.color = "var(--color-brand)";
        crmTabSendBtn.style.borderBottomColor = "var(--color-brand)";
        crmTabReportsBtn.style.color = "var(--text-muted)";
        crmTabReportsBtn.style.borderBottomColor = "transparent";
        
        crmSendPanel.style.display = "block";
        crmReportsPanel.style.display = "none";
    });

    crmTabReportsBtn.addEventListener("click", () => {
        crmTabReportsBtn.classList.add("active");
        crmTabSendBtn.classList.remove("active");
        crmTabReportsBtn.style.color = "var(--color-brand)";
        crmTabReportsBtn.style.borderBottomColor = "var(--color-brand)";
        crmTabSendBtn.style.color = "var(--text-muted)";
        crmTabSendBtn.style.borderBottomColor = "transparent";

        crmSendPanel.style.display = "none";
        crmReportsPanel.style.display = "flex";
        
        appRouter.loadCampaigns();
    });
}


// Billing Action Event Listeners
const selectBillingMode = document.getElementById("select-billing-mode");
if (selectBillingMode) {
    selectBillingMode.addEventListener("change", async (e) => {
        const billing_mode = e.target.value;
        try {
            await api.post("/api/billing/mode", { billing_mode });
            showToast("Método de cobrança atualizado com sucesso!", "success");
            appRouter.loadBillingSummary();
        } catch (err) {
            showToast("Erro ao alterar método de faturamento: " + err.message, "error");
        }
    });
}

const btnBillingRecharge = document.getElementById("btn-billing-recharge");
if (btnBillingRecharge) {
    let pollingInterval = null;

    btnBillingRecharge.addEventListener("click", async () => {
        const amountInput = document.getElementById("billing-recharge-amount");
        const amount = parseFloat(amountInput.value) || 0;

        if (amount <= 0) {
            showToast("Insira um valor maior que zero para recarga.", "error");
            return;
        }

        const originalText = btnBillingRecharge.innerHTML;
        btnBillingRecharge.disabled = true;
        btnBillingRecharge.innerHTML = "<i class='fa-solid fa-spinner fa-spin'></i> Processando...";

        try {
            const res = await api.post(`/api/billing/recharge?amount=${amount}`, {});
            if (res && res.success) {
                // Abre o modal Pix
                const pixModal = document.getElementById("pix-payment-modal");
                const qrCodeImg = document.getElementById("pix-qr-code-img");
                const copiaColaInput = document.getElementById("pix-copia-cola-input");
                
                if (pixModal && qrCodeImg && copiaColaInput) {
                    qrCodeImg.src = `data:image/png;base64,${res.qrCodeBase64}`;
                    copiaColaInput.value = res.qrCode;
                    pixModal.style.display = "flex";

                    // Limpa polling anterior se existir
                    if (pollingInterval) clearInterval(pollingInterval);

                    // Polling de 3 em 3 segundos para validar status
                    pollingInterval = setInterval(async () => {
                        try {
                            const statusRes = await api.get(`/api/billing/recharge/status/${res.paymentId}`);
                            if (statusRes && statusRes.status === "approved") {
                                clearInterval(pollingInterval);
                                showToast("Pagamento Pix aprovado! Créditos liberados.", "success");
                                pixModal.style.display = "none";
                                appRouter.loadBillingSummary();
                            }
                        } catch (err) {
                            console.error("Erro no polling do Pix:", err);
                        }
                    }, 3000);
                }
            } else {
                showToast("Erro ao criar pagamento Pix.", "error");
            }
        } catch (err) {
            showToast("Erro ao processar recarga: " + err.message, "error");
        } finally {
            btnBillingRecharge.disabled = false;
            btnBillingRecharge.innerHTML = originalText;
        }
    });

    // Close Pix Modal button
    const btnClosePix = document.getElementById("btn-close-pix-modal");
    if (btnClosePix) {
        btnClosePix.addEventListener("click", () => {
            const pixModal = document.getElementById("pix-payment-modal");
            if (pixModal) pixModal.style.display = "none";
            if (pollingInterval) clearInterval(pollingInterval);
            appRouter.loadBillingSummary();
        });
    }

    // Copy Pix Copia e Cola button
    const btnCopyPix = document.getElementById("btn-copy-pix");
    if (btnCopyPix) {
        btnCopyPix.addEventListener("click", () => {
            const input = document.getElementById("pix-copia-cola-input");
            if (input && input.value) {
                navigator.clipboard.writeText(input.value);
                showToast("Código Pix Copia e Cola copiado!", "success");
            }
        });
    }
}


// Start router
window.appRouter = appRouter;
appRouter.init();

// ============================================================
// Helper: Navegar para tab por nome (usado nos cards de integração)
// ============================================================
appRouter.selectTabByName = function(viewName) {
    document.querySelectorAll(".menu-item").forEach(i => i.classList.remove("active"));
    const target = document.querySelector(`[data-target="${viewName}"]`);
    if (target) target.classList.add("active");
    document.querySelectorAll(".workspace-view").forEach(v => v.classList.remove("active"));
    const view = document.getElementById(viewName);
    if (view) view.classList.add("active");
    if (viewName === "settings-view") appRouter.loadSettings();
};

// ============================================================
// Renderização de views a partir de <script type="text/template">
// ============================================================
function renderTemplateView(templateId, viewId) {
    let view = document.getElementById(viewId);
    if (!view) {
        const tpl = document.getElementById(templateId);
        if (!tpl) return;
        view = document.createElement("div");
        view.id = viewId;
        view.className = "workspace-view";
        view.innerHTML = tpl.innerHTML;
        document.querySelector("main.workspace").appendChild(view);
    }
    document.querySelectorAll(".workspace-view").forEach(v => v.classList.remove("active"));
    view.classList.add("active");
}

// Patch: intercept selectTab para views de template
const _origSelectTab = appRouter.selectTab.bind(appRouter);
appRouter.selectTab = function(event) {
    const target = event.currentTarget.getAttribute("data-target");
    if (target === "integrations-view") {
        event.preventDefault();
        document.querySelectorAll(".menu-item").forEach(i => i.classList.remove("active"));
        event.currentTarget.classList.add("active");
        renderTemplateView("tpl-integrations-view", "integrations-view");
        return;
    }
    if (target === "email-view") {
        event.preventDefault();
        document.querySelectorAll(".menu-item").forEach(i => i.classList.remove("active"));
        event.currentTarget.classList.add("active");
        renderTemplateView("tpl-email-view", "email-view");
        return;
    }
    _origSelectTab(event);
};

// ============================================================
// UI Helpers — Modal, Email tabs, SMTP, Notifications
// ============================================================
const uiHelpers = {

    // --- Modal "Em Desenvolvimento" ---
    showDevModal(title, description) {
        document.getElementById("dev-modal-title").innerText = title;
        document.getElementById("dev-modal-description").innerText = description;
        document.getElementById("dev-modal-overlay").classList.add("active");
    },
    closeDevModal() {
        document.getElementById("dev-modal-overlay").classList.remove("active");
    },

    // --- Email Sub-tabs ---
    switchEmailTab(event) {
        const targetTabId = event.currentTarget.getAttribute("data-email-tab");
        document.querySelectorAll(".email-tab-btn").forEach(b => b.classList.remove("active"));
        document.querySelectorAll(".email-tab-content").forEach(c => c.classList.remove("active"));
        event.currentTarget.classList.add("active");
        const tab = document.getElementById(targetTabId);
        if (tab) tab.classList.add("active");
    },

    // --- SMTP ---
    onSmtpProviderChange() {
        const provider = document.getElementById("smtp-provider")?.value;
        const presets = {
            sendgrid: { host: "smtp.sendgrid.net",                      port: 587 },
            mailgun:  { host: "smtp.mailgun.org",                       port: 587 },
            ses:      { host: "email-smtp.us-east-1.amazonaws.com",     port: 587 },
            resend:   { host: "smtp.resend.com",                        port: 587 },
            custom:   { host: "",                                        port: 587 },
        };
        const p = presets[provider] || presets.custom;
        const h = document.getElementById("smtp-host");
        const po = document.getElementById("smtp-port");
        if (h) h.value = p.host;
        if (po) po.value = p.port;
    },

    testSmtpConnection() {
        // TODO: POST /api/email/test-smtp
        const el = document.getElementById("smtp-status");
        if (!el) return;
        el.innerHTML = `<span class="status-dot" style="background:var(--color-warning);"></span><span style="font-size:13px;">Testando conexão...</span>`;
        setTimeout(() => {
            el.innerHTML = `<span class="status-dot status-unconfigured"></span><span style="font-size:13px;">Configure o SMTP para testar</span>`;
        }, 2000);
    },

    saveSmtpConfig() {
        // TODO: POST /api/email/smtp-config
        const config = {
            provider:   document.getElementById("smtp-provider")?.value,
            host:       document.getElementById("smtp-host")?.value,
            port:       document.getElementById("smtp-port")?.value,
            security:   document.getElementById("smtp-security")?.value,
            user:       document.getElementById("smtp-user")?.value,
            from_email: document.getElementById("smtp-from-email")?.value,
            from_name:  document.getElementById("smtp-from-name")?.value,
        };
        console.log("[SMTP Config - Pendente Backend]", config);
        showToast("Configuração SMTP salva! Integração com backend em breve.", "success");
    },

    // --- E-mail Marketing ---
    openNewEmailCampaign() {
        const list = document.getElementById("email-campaign-list");
        const builder = document.getElementById("email-campaign-builder");
        if (list) list.style.display = "none";
        if (builder) { builder.style.display = "flex"; builder.style.flexDirection = "column"; }
    },

    previewEmailCampaign() {
        const subject = document.getElementById("email-campaign-subject")?.value;
        const body = document.getElementById("email-campaign-body")?.value;
        if (!subject || !body) { showToast("Preencha o assunto e o corpo do e-mail.", "error"); return; }
        alert(`📧 Preview:\n\nAssunto: ${subject}\n\nCorpo:\n${body.substring(0, 300)}...`);
    },

    saveEmailCampaign() {
        // TODO: POST /api/email/campaigns
        const campaign = {
            name:           document.getElementById("email-campaign-name")?.value,
            subject:        document.getElementById("email-campaign-subject")?.value,
            recipient_list: document.getElementById("email-campaign-list-select")?.value,
            body:           document.getElementById("email-campaign-body")?.value,
            schedule:       document.getElementById("email-campaign-schedule")?.value,
        };
        if (!campaign.name || !campaign.subject || !campaign.body) {
            showToast("Preencha todos os campos obrigatórios.", "error"); return;
        }
        console.log("[Email Campaign - Pendente Backend]", campaign);
        showToast("Campanha salva! Envio via SMTP será integrado em breve.", "success");
    },

    // --- Notificações do Sistema ---
    saveNotificationSettings() {
        // TODO: POST /api/email/notification-settings
        const settings = {
            balance:      { enabled: document.getElementById("notif-balance")?.checked,      threshold: document.getElementById("notif-balance-threshold")?.value, email: document.getElementById("notif-balance-email")?.value,  subject: document.getElementById("notif-balance-subject")?.value },
            subscription: { enabled: document.getElementById("notif-subscription")?.checked, days_before: document.getElementById("notif-sub-days")?.value,        email: document.getElementById("notif-sub-email")?.value,       subject: document.getElementById("notif-sub-subject")?.value },
            welcome:      { enabled: document.getElementById("notif-welcome")?.checked,      subject: document.getElementById("notif-welcome-subject")?.value },
            suspended:    { enabled: document.getElementById("notif-suspended")?.checked,    subject: document.getElementById("notif-suspended-subject")?.value },
        };
        console.log("[Notification Settings - Pendente Backend]", settings);
        showToast("Configurações de notificação salvas com sucesso!", "success");
    },

    // --- 🔒 Chat Mode (Mensagem vs Nota Interna) ---
    setChatMode(mode) {
        state.chatMode = mode;
        const msgTab = document.getElementById("tab-mode-msg");
        const noteTab = document.getElementById("tab-mode-note");
        const input = document.getElementById("chat-message-input");
        const sendBtn = document.querySelector(".btn-send");
        
        if (mode === "note") {
            if (msgTab) msgTab.classList.remove("active-mode");
            if (noteTab) noteTab.classList.add("active-mode");
            if (input) {
                input.placeholder = "🔒 Digite uma anotação interna visível apenas para a equipe (não será enviada para o WhatsApp)...";
                input.style.borderColor = "#f59e0b";
            }
            if (sendBtn) {
                sendBtn.innerHTML = `<i class="fa-solid fa-lock"></i>`;
                sendBtn.style.background = "#f59e0b";
            }
        } else {
            if (msgTab) msgTab.classList.add("active-mode");
            if (noteTab) noteTab.classList.remove("active-mode");
            if (input) {
                input.placeholder = "Digite uma mensagem, cole imagens (Ctrl+V) ou digite / para respostas rápidas...";
                input.style.borderColor = "var(--border-color)";
            }
            if (sendBtn) {
                sendBtn.innerHTML = `<i class="fa-solid fa-paper-plane"></i>`;
                sendBtn.style.background = "var(--color-brand)";
            }
        }
    },

    // --- 🏷️ Gerenciamento de Tags / Etiquetas ---
    async openTagSelectorModal() {
        if (!state.activeConversationId) return;
        const modal = document.getElementById("tags-manager-modal");
        if (!modal) return;
        modal.style.display = "flex";
        await this.loadAndRenderTagsModal();
    },

    closeTagsModal() {
        const modal = document.getElementById("tags-manager-modal");
        if (modal) modal.style.display = "none";
    },

    async loadAndRenderTagsModal() {
        const listEl = document.getElementById("modal-tags-selector-list");
        if (!listEl) return;
        listEl.innerHTML = `<p style="font-size:12px;color:var(--text-muted);"><i class="fa-solid fa-spinner fa-spin"></i> Carregando etiquetas...</p>`;
        
        try {
            const allTags = await api.get("/api/inbox/tags");
            state.cachedTags = allTags;
            
            const convo = state.conversations.find(c => c.id === state.activeConversationId);
            const activeTagIds = new Set((convo?.tags || []).map(t => t.id));
            
            if (allTags.length === 0) {
                listEl.innerHTML = `<p style="font-size:12px;color:var(--text-muted);margin:4px 0;">Nenhuma etiqueta criada ainda. Crie uma abaixo!</p>`;
                return;
            }
            
            listEl.innerHTML = "";
            allTags.forEach(tag => {
                const isSelected = activeTagIds.has(tag.id);
                const item = document.createElement("div");
                item.className = `tag-selectable-item ${isSelected ? 'selected' : ''}`;
                item.style.cssText = `background: ${tag.color || '#6366f1'}; color: #fff; padding: 4px 10px; border-radius: 16px; font-size: 11px; font-weight: 700; display: inline-flex; align-items: center; gap: 6px; cursor: pointer;`;
                item.innerHTML = `
                    <i class="fa-solid ${isSelected ? 'fa-check' : 'fa-tag'}" style="font-size: 10px;"></i>
                    <span>${escapeHTML(tag.name)}</span>
                `;
                item.onclick = () => uiHelpers.toggleTagInActiveConvo(tag);
                listEl.appendChild(item);
            });
        } catch (err) {
            listEl.innerHTML = `<p style="font-size:12px;color:#ef4444;">Erro ao carregar tags: ${err.message}</p>`;
        }
    },

    async toggleTagInActiveConvo(tag) {
        if (!state.activeConversationId) return;
        const convo = state.conversations.find(c => c.id === state.activeConversationId);
        if (!convo) return;
        
        if (!convo.tags) convo.tags = [];
        const exists = convo.tags.some(t => t.id === tag.id);
        
        try {
            if (exists) {
                await api.delete(`/api/inbox/conversations/${state.activeConversationId}/tags/${tag.id}`);
                convo.tags = convo.tags.filter(t => t.id !== tag.id);
            } else {
                await api.post(`/api/inbox/conversations/${state.activeConversationId}/tags/${tag.id}`, {});
                convo.tags.push(tag);
            }
            
            // Re-render chat header tags
            const tagsListEl = document.getElementById("active-chat-tags-list");
            if (tagsListEl) {
                tagsListEl.innerHTML = "";
                convo.tags.forEach(t => {
                    const tagBadge = document.createElement("span");
                    tagBadge.className = "tag-badge";
                    tagBadge.style.background = t.color || "#6366f1";
                    tagBadge.innerHTML = `
                        <span>${escapeHTML(t.name)}</span>
                        <i class="fa-solid fa-xmark tag-remove-btn" onclick="event.stopPropagation(); uiHelpers.removeTagFromActiveConvo('${t.id}')"></i>
                    `;
                    tagsListEl.appendChild(tagBadge);
                });
            }
            
            await this.loadAndRenderTagsModal();
        } catch (err) {
            showToast("Erro ao atualizar tag: " + err.message, "error");
        }
    },

    async removeTagFromActiveConvo(tagId) {
        if (!state.activeConversationId) return;
        const convo = state.conversations.find(c => c.id === state.activeConversationId);
        if (!convo) return;
        
        try {
            await api.delete(`/api/inbox/conversations/${state.activeConversationId}/tags/${tagId}`);
            convo.tags = (convo.tags || []).filter(t => t.id !== tagId);
            
            const tagsListEl = document.getElementById("active-chat-tags-list");
            if (tagsListEl) {
                tagsListEl.innerHTML = "";
                convo.tags.forEach(t => {
                    const tagBadge = document.createElement("span");
                    tagBadge.className = "tag-badge";
                    tagBadge.style.background = t.color || "#6366f1";
                    tagBadge.innerHTML = `
                        <span>${escapeHTML(t.name)}</span>
                        <i class="fa-solid fa-xmark tag-remove-btn" onclick="event.stopPropagation(); uiHelpers.removeTagFromActiveConvo('${t.id}')"></i>
                    `;
                    tagsListEl.appendChild(tagBadge);
                });
            }
        } catch (err) {
            showToast("Erro ao remover tag: " + err.message, "error");
        }
    },

    async createNewTag() {
        const nameInput = document.getElementById("new-tag-name");
        const colorInput = document.getElementById("new-tag-color");
        const name = (nameInput?.value || "").trim();
        const color = colorInput?.value || "#6366f1";
        
        if (!name) {
            showToast("Informe o nome da tag.", "warning");
            return;
        }
        
        try {
            const newTag = await api.post("/api/inbox/tags", { name, color });
            nameInput.value = "";
            showToast(`Etiqueta "${newTag.name}" criada com sucesso!`, "success");
            if (state.activeConversationId) {
                await this.toggleTagInActiveConvo(newTag);
            } else {
                await this.loadAndRenderTagsModal();
            }
        } catch (err) {
            showToast("Erro ao criar tag: " + err.message, "error");
        }
    },

    // --- ⭐ Resolução com CSAT ---
    openResolveCSATModal() {
        if (!state.activeConversationId) return;
        const modal = document.getElementById("resolve-csat-modal");
        if (modal) modal.style.display = "flex";
    },

    closeResolveCSATModal() {
        const modal = document.getElementById("resolve-csat-modal");
        if (modal) modal.style.display = "none";
    },

    async confirmResolveWithCSAT() {
        if (!state.activeConversationId) return;
        const sendCsat = document.getElementById("resolve-send-csat-checkbox")?.checked ?? true;
        const btn = document.getElementById("btn-confirm-resolve-csat");
        if (btn) btn.disabled = true;
        
        try {
            await api.post(`/api/inbox/conversations/${state.activeConversationId}/resolve-with-csat`, {
                send_csat: sendCsat
            });
            showToast(sendCsat ? "Conversa resolvida e Pesquisa CSAT enviada!" : "Conversa resolvida com sucesso!", "success");
            this.closeResolveCSATModal();
            
            // Recarrega conversas
            await appRouter.loadConversations("resolved");
            // Atualiza status local
            const convo = state.conversations.find(c => c.id === state.activeConversationId);
            if (convo) convo.status = "resolved";
            const statusEl = document.getElementById("active-contact-status");
            if (statusEl) statusEl.innerText = "✔ Resolvida";
        } catch (err) {
            showToast("Erro ao resolver conversa: " + err.message, "error");
        } finally {
            if (btn) btn.disabled = false;
        }
    },

    // --- 🔍 Busca Global de Mensagens ---
    openGlobalSearchModal() {
        const modal = document.getElementById("global-search-modal");
        if (modal) {
            modal.style.display = "flex";
            const input = document.getElementById("global-search-query-input");
            if (input) {
                input.value = "";
                input.focus();
            }
            document.getElementById("global-search-results-list").innerHTML = `<p style="text-align: center; color: var(--text-muted); padding: 40px 0; font-size: 13px;">Digite ao menos 2 caracteres para pesquisar em todo o histórico de mensagens.</p>`;
        }
    },

    closeGlobalSearchModal() {
        const modal = document.getElementById("global-search-modal");
        if (modal) modal.style.display = "none";
    },

    _searchDebounceTimer: null,
    handleGlobalSearchInput(val) {
        clearTimeout(this._searchDebounceTimer);
        const q = (val || "").trim();
        const resultsEl = document.getElementById("global-search-results-list");
        if (!resultsEl) return;
        
        if (q.length < 2) {
            resultsEl.innerHTML = `<p style="text-align: center; color: var(--text-muted); padding: 40px 0; font-size: 13px;">Digite ao menos 2 caracteres para pesquisar.</p>`;
            return;
        }
        
        resultsEl.innerHTML = `<p style="text-align: center; color: var(--text-muted); padding: 40px 0; font-size: 13px;"><i class="fa-solid fa-spinner fa-spin"></i> Pesquisando no histórico...</p>`;
        
        this._searchDebounceTimer = setTimeout(async () => {
            try {
                const results = await api.get(`/api/inbox/messages/search?q=${encodeURIComponent(q)}`);
                if (!results || results.length === 0) {
                    resultsEl.innerHTML = `<p style="text-align: center; color: var(--text-muted); padding: 40px 0; font-size: 13px;">Nenhuma mensagem encontrada para "<strong>${escapeHTML(q)}</strong>".</p>`;
                    return;
                }
                
                resultsEl.innerHTML = "";
                results.forEach(res => {
                    const card = document.createElement("div");
                    card.className = "global-search-result-card";
                    
                    let cleanStr = res.matched_at ? String(res.matched_at) : "";
                    if (cleanStr && !cleanStr.endsWith("Z") && !cleanStr.includes("+") && !cleanStr.includes("-")) cleanStr += "Z";
                    const d = cleanStr ? new Date(cleanStr) : null;
                    const dateStr = d ? `${d.toLocaleDateString('pt-BR')} às ${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}` : "";
                    
                    const senderLabel = res.is_note ? "🔒 Nota Interna" : (res.sender_type === "contact" ? "👤 Cliente" : "💼 Atendente");
                    
                    card.innerHTML = `
                        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 4px;">
                            <strong style="font-size: 13px; color: var(--text-primary);">${escapeHTML(res.contact_name)} <span style="font-weight: 400; color: var(--text-muted); font-size: 11px;">(${res.phone_number})</span></strong>
                            <span style="font-size: 10px; color: var(--text-muted);">${dateStr}</span>
                        </div>
                        <div style="font-size: 12px; color: var(--text-secondary); line-height: 1.4; margin-bottom: 4px; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;">
                            <span style="font-weight: 700; color: ${res.is_note ? '#fbbf24' : 'var(--color-brand)'};">${senderLabel}:</span> ${escapeHTML(res.snippet)}
                        </div>
                    `;
                    card.onclick = () => {
                        uiHelpers.closeGlobalSearchModal();
                        appRouter.selectTabByName("inbox-view");
                        appRouter.selectConversation(res.conversation_id);
                    };
                    resultsEl.appendChild(card);
                });
            } catch (err) {
                resultsEl.innerHTML = `<p style="text-align: center; color: #ef4444; padding: 40px 0; font-size: 13px;">Erro na pesquisa: ${err.message}</p>`;
            }
        }, 300);
    }
};

window.uiHelpers = uiHelpers;

// --- 📌 KANBAN CRM PIPELINE WITH DRAG & DROP ---
appRouter.loadKanbanBoard = async function() {
    const container = document.getElementById("kanban-board-container");
    if (!container) return;
    
    container.innerHTML = `<div style="text-align:center;padding:60px;width:100%;color:var(--text-muted);"><i class="fa-solid fa-spinner fa-spin" style="font-size:24px;"></i><p style="margin-top:10px;">Carregando pipeline de oportunidades...</p></div>`;
    
    try {
        const data = await api.get("/api/inbox/crm/kanban");
        
        // Atualiza resumo no header
        const grandTotalEl = document.getElementById("kanban-grand-total");
        const grandDealsEl = document.getElementById("kanban-deals-count");
        if (grandTotalEl) grandTotalEl.innerText = `R$ ${data.grand_total_value.toLocaleString('pt-BR', { minimumFractionDigits: 2 })}`;
        if (grandDealsEl) grandDealsEl.innerText = data.grand_total_deals;
        
        container.innerHTML = "";
        
        data.columns.forEach(col => {
            const colEl = document.createElement("div");
            colEl.className = "kanban-col";
            colEl.setAttribute("data-stage", col.stage);
            
            colEl.innerHTML = `
                <div class="kanban-col-header">
                    <div>
                        <div class="kanban-col-title">
                            <span>${col.label}</span>
                            <span class="kanban-col-count">${col.total_deals}</span>
                        </div>
                        <div class="kanban-col-total-val">R$ ${col.total_value.toLocaleString('pt-BR', { minimumFractionDigits: 2 })}</div>
                    </div>
                </div>
                <div class="kanban-cards-wrapper" id="kanban-cards-${col.stage}"></div>
            `;
            
            // Drag & drop handlers on column
            const cardsWrapper = colEl.querySelector(".kanban-cards-wrapper");
            
            colEl.addEventListener("dragover", (e) => {
                e.preventDefault();
                colEl.classList.add("drag-over");
            });
            
            colEl.addEventListener("dragleave", (e) => {
                colEl.classList.remove("drag-over");
            });
            
            colEl.addEventListener("drop", async (e) => {
                e.preventDefault();
                colEl.classList.remove("drag-over");
                const contactId = e.dataTransfer.getData("text/plain");
                if (!contactId) return;
                
                try {
                    await api.patch(`/api/inbox/contacts/${contactId}/kanban-stage`, {
                        kanban_stage: col.stage
                    });
                    showToast(`Card movido para "${col.label}"!`, "success");
                    appRouter.loadKanbanBoard();
                } catch (err) {
                    showToast("Erro ao mover card: " + err.message, "error");
                }
            });
            
            // Render cards
            col.cards.forEach(card => {
                const cardEl = document.createElement("div");
                cardEl.className = "kanban-card";
                cardEl.draggable = true;
                cardEl.setAttribute("data-contact-id", card.contact_id);
                
                cardEl.addEventListener("dragstart", (e) => {
                    e.dataTransfer.setData("text/plain", card.contact_id);
                    cardEl.classList.add("dragging");
                });
                
                cardEl.addEventListener("dragend", () => {
                    cardEl.classList.remove("dragging");
                });
                
                const tagsHtml = card.tags.map(t => `<span class="tag-badge" style="background:${t.color || '#6366f1'};font-size:9px;padding:1px 5px;">${escapeHTML(t.name)}</span>`).join("");
                
                cardEl.innerHTML = `
                    <div class="kanban-card-name">
                        <span>${escapeHTML(card.name)}</span>
                        <span class="kanban-deal-badge">R$ ${card.deal_value.toLocaleString('pt-BR', { minimumFractionDigits: 2 })}</span>
                    </div>
                    <div class="kanban-card-phone"><i class="fa-brands fa-whatsapp" style="color:#22c55e;margin-right:4px;"></i>${escapeHTML(card.phone_number)}</div>
                    <div class="kanban-card-snippet">${escapeHTML(card.last_message || 'Nenhuma mensagem recente')}</div>
                    ${tagsHtml ? `<div style="display:flex;gap:4px;flex-wrap:wrap;margin-bottom:8px;">${tagsHtml}</div>` : ''}
                    <div class="kanban-card-footer">
                        <span style="color:var(--text-muted);">${card.assigned_agent_name ? `👤 ${card.assigned_agent_name}` : 'Sem atendente'}</span>
                        <button class="btn btn-secondary btn-xs" style="padding:2px 8px;font-size:10px;" onclick="event.stopPropagation(); appRouter.openChatFromKanban('${card.id}')">
                            <i class="fa-solid fa-comments"></i> Abrir Chat
                        </button>
                    </div>
                `;
                
                cardEl.onclick = () => appRouter.openChatFromKanban(card.id);
                cardsWrapper.appendChild(cardEl);
            });
            
            container.appendChild(colEl);
        });
    } catch (err) {
        container.innerHTML = `<div style="text-align:center;padding:60px;width:100%;color:#ef4444;"><p>Erro ao carregar Kanban: ${err.message}</p></div>`;
    }
};

appRouter.openChatFromKanban = function(convoId) {
    appRouter.selectTabByName("inbox-view");
    appRouter.selectConversation(convoId);
};

// ============================================================
// TYPEBOT FLOW BUILDER ENGINE
// ============================================================
appRouter.switchBotMode = function(mode) {
    document.querySelectorAll(".bot-mode-tab-btn").forEach(b => {
        b.classList.remove("active");
        b.style.color = "var(--text-muted)";
        b.style.borderBottomColor = "transparent";
    });

    const activeBtn = document.getElementById(`bot-mode-${mode}`);
    if (activeBtn) {
        activeBtn.classList.add("active");
        activeBtn.style.color = "var(--color-brand)";
        activeBtn.style.borderBottomColor = "var(--color-brand)";
    }

    document.getElementById("bot-panel-builder").style.display = mode === "builder" ? "flex" : "none";
    document.getElementById("bot-panel-simple").style.display = mode === "simple" ? "block" : "none";

    if (mode === "builder" && !window.botFlowBuilder.initialized) {
        window.botFlowBuilder.init();
    }
};

const botFlowBuilder = {
    initialized: false,
    nodes: [],
    connections: [],
    selectedNodeId: null,
    connectingFromNodeId: null,
    nextId: 1,

    init() {
        this.initialized = true;
        this.loadFlow();
    },

    async loadFlow() {
        try {
            const config = await api.get("/api/inbox/bot-config");
            if (config && config.flow_data && config.flow_data.nodes && config.flow_data.nodes.length > 0) {
                this.nodes = config.flow_data.nodes;
                this.connections = config.flow_data.connections || [];
                this.nextId = Math.max(...this.nodes.map(n => parseInt(n.id) || 0), 0) + 1;
            } else {
                // Default starter nodes
                this.nodes = [
                    { id: "1", type: "start", title: "Início", x: 60, y: 100, content: "Cliente envia primeira mensagem" },
                    { id: "2", type: "text", title: "Mensagem de Boas-Vindas", x: 320, y: 100, content: (config && config.welcome_message) ? config.welcome_message : "Olá! Seja bem-vindo ao nosso atendimento." }
                ];
                this.connections = [{ from: "1", to: "2" }];
                this.nextId = 3;
            }
            this.render();
        } catch (e) {
            console.error("Erro ao carregar fluxo do bot:", e);
        }
    },

    addNode(type) {
        const typesConfig = {
            text:      { title: "Mensagem de Texto", color: "#3b82f6", icon: "fa-comment-dots", content: "Olá! Como posso ajudar você hoje?" },
            buttons:   { title: "Menu de Opções",     color: "#8b5cf6", icon: "fa-list-check",   content: "Escolha uma opção:\n1. Reservas\n2. Recepção\n3. Falar com Atendente" },
            input:     { title: "Capturar Resposta", color: "#f59e0b", icon: "fa-pen-to-square", content: "Aguardar nome ou data de entrada" },
            condition: { title: "Regra Condicional",  color: "#ec4899", icon: "fa-code-branch",  content: "Se mensagem contém 'reserva'" },
            transfer:  { title: "Transferir Fila",    color: "#10b981", icon: "fa-headset",      content: "Encaminhar para atendimento humano" }
        };

        const cfg = typesConfig[type] || typesConfig.text;
        const newId = String(this.nextId++);
        const offset = (this.nodes.length * 30) % 200;

        const newNode = {
            id: newId,
            type: type,
            title: cfg.title,
            color: cfg.color,
            icon: cfg.icon,
            x: 100 + offset,
            y: 150 + offset,
            content: cfg.content
        };

        this.nodes.push(newNode);
        this.render();
        this.selectNode(newId);
    },

    selectNode(id) {
        this.selectedNodeId = id;
        document.querySelectorAll(".flow-node").forEach(n => n.classList.remove("selected"));
        const el = document.getElementById(`node-${id}`);
        if (el) el.classList.add("selected");
        this.openInspector(id);
    },

    openInspector(id) {
        const node = this.nodes.find(n => n.id === id);
        if (!node) return;

        const inspector = document.getElementById("flow-inspector");
        const titleEl = document.getElementById("inspector-title");
        const contentEl = document.getElementById("inspector-content");

        titleEl.innerText = `Editar ${node.title}`;
        inspector.style.display = "flex";

        contentEl.innerHTML = `
            <div class="input-group">
                <label style="font-size:11px;font-weight:700;color:var(--text-muted);text-transform:uppercase;">Título do Bloco</label>
                <input type="text" id="insp-title-input" value="${node.title}" style="width:100%;padding:8px;border-radius:6px;border:1px solid var(--border-color);background:var(--bg-primary);color:var(--text-primary);font-size:13px;" oninput="botFlowBuilder.updateNodeProp('${id}', 'title', this.value)">
            </div>
            <div class="input-group" style="margin-top:12px;">
                <label style="font-size:11px;font-weight:700;color:var(--text-muted);text-transform:uppercase;">Conteúdo / Mensagem</label>
                <textarea id="insp-content-input" rows="5" style="width:100%;padding:8px;border-radius:6px;border:1px solid var(--border-color);background:var(--bg-primary);color:var(--text-primary);font-size:13px;" oninput="botFlowBuilder.updateNodeProp('${id}', 'content', this.value)">${node.content || ""}</textarea>
            </div>
            <div style="margin-top:16px;padding:10px;background:rgba(99,102,241,0.1);border:1px solid rgba(99,102,241,0.2);border-radius:6px;font-size:12px;color:var(--text-secondary);">
                <i class="fa-solid fa-circle-info" style="color:var(--color-brand);margin-right:4px;"></i>
                Clique no bolinha inferior de saída e depois na bolinha superior de outro bloco para ligar.
            </div>
            ${node.type !== "start" ? `
            <button class="btn btn-secondary btn-sm" style="margin-top:20px;color:#ef4444;border-color:#ef4444;width:100%;" onclick="botFlowBuilder.deleteNode('${id}')">
                <i class="fa-solid fa-trash"></i> Excluir Bloco
            </button>` : ""}
        `;
    },

    closeInspector() {
        document.getElementById("flow-inspector").style.display = "none";
        this.selectedNodeId = null;
        document.querySelectorAll(".flow-node").forEach(n => n.classList.remove("selected"));
    },

    updateNodeProp(id, prop, val) {
        const node = this.nodes.find(n => n.id === id);
        if (!node) return;
        node[prop] = val;
        const textEl = document.getElementById(`node-body-${id}`);
        const headerTitleEl = document.getElementById(`node-header-title-${id}`);
        if (prop === "content" && textEl) textEl.innerText = val;
        if (prop === "title" && headerTitleEl) headerTitleEl.innerText = val;
    },

    deleteNode(id) {
        this.nodes = this.nodes.filter(n => n.id !== id);
        this.connections = this.connections.filter(c => c.from !== id && c.to !== id);
        this.closeInspector();
        this.render();
    },

    clearFlow() {
        if (confirm("Deseja realmente limpar todo o fluxo de nós?")) {
            this.nodes = [{ id: "1", type: "start", title: "Início", x: 60, y: 100, content: "Cliente envia primeira mensagem" }];
            this.connections = [];
            this.nextId = 2;
            this.closeInspector();
            this.render();
        }
    },

    // Handle Output/Input Port Connection
    onPortClick(nodeId, type) {
        if (type === "output") {
            this.connectingFromNodeId = nodeId;
            showToast(`Clique na bolinha superior de outro bloco para conectar.`, "info");
        } else if (type === "input" && this.connectingFromNodeId) {
            if (this.connectingFromNodeId === nodeId) {
                showToast("Não é possível conectar um bloco a ele mesmo.", "warning");
                this.connectingFromNodeId = null;
                return;
            }
            // Check if connection already exists
            const exists = this.connections.some(c => c.from === this.connectingFromNodeId && c.to === nodeId);
            if (!exists) {
                this.connections.push({ from: this.connectingFromNodeId, to: nodeId });
                showToast("Blocos conectados com sucesso!");
            }
            this.connectingFromNodeId = null;
            this.render();
        }
    },

    removeConnection(fromId, toId) {
        this.connections = this.connections.filter(c => !(c.from === fromId && c.to === toId));
        this.render();
    },

    async saveFlow() {
        try {
            const flowData = {
                nodes: this.nodes,
                connections: this.connections
            };
            const firstTextNode = this.nodes.find(n => n.type === "text");
            const payload = {
                flow_data: flowData,
                welcome_message: firstTextNode ? firstTextNode.content : undefined
            };
            const res = await api.post("/api/inbox/bot-config", payload);
            if (res) {
                showToast("Fluxo do Bot salvo com sucesso!", "success");
            }
        } catch (e) {
            console.error("Erro ao salvar fluxo:", e);
            showToast("Erro ao salvar o fluxo visual do bot.", "error");
        }
    },

    render() {
        const container = document.getElementById("flow-nodes-container");
        const svg = document.getElementById("flow-svg");
        if (!container || !svg) return;

        container.innerHTML = "";
        svg.innerHTML = "";

        // Render Connections (Lines)
        this.connections.forEach(conn => {
            const fromNode = this.nodes.find(n => n.id === conn.from);
            const toNode = this.nodes.find(n => n.id === conn.to);
            if (fromNode && toNode) {
                const x1 = fromNode.x + 110;
                const y1 = fromNode.y + 110;
                const x2 = toNode.x + 110;
                const y2 = toNode.y;

                const group = document.createElementNS("http://www.w3.org/2000/svg", "g");
                group.style.cursor = "pointer";

                const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
                const d = `M ${x1} ${y1} C ${x1} ${y1 + 60}, ${x2} ${y2 - 60}, ${x2} ${y2}`;
                path.setAttribute("d", d);
                path.setAttribute("stroke", "#6366f1");
                path.setAttribute("stroke-width", "3");
                path.setAttribute("fill", "none");
                path.setAttribute("stroke-dasharray", "6 4");
                path.style.pointerEvents = "all";

                // Click connection line to delete
                path.addEventListener("click", () => {
                    if (confirm("Remover esta conexão entre os blocos?")) {
                        this.removeConnection(conn.from, conn.to);
                    }
                });

                group.appendChild(path);
                svg.appendChild(group);
            }
        });

        // Render Nodes
        this.nodes.forEach(node => {
            const el = document.createElement("div");
            el.className = `flow-node ${this.selectedNodeId === node.id ? "selected" : ""}`;
            el.id = `node-${node.id}`;
            el.style.left = `${node.x}px`;
            el.style.top = `${node.y}px`;

            const iconClass = node.icon || (node.type === "start" ? "fa-play" : "fa-cube");
            const headerColor = node.color || "#475569";

            el.innerHTML = `
                <div class="flow-node-header" style="background:${headerColor};">
                    <span style="display:flex;align-items:center;gap:6px;" id="node-header-title-${node.id}">
                        <i class="fa-solid ${iconClass}"></i> ${node.title}
                    </span>
                    <span style="opacity:0.6;font-size:10px;">#${node.id}</span>
                </div>
                <div class="flow-node-body" id="node-body-${node.id}">${node.content || ""}</div>
                ${node.type !== "start" ? `<div class="flow-node-port input-port" title="Entrada (Conectar aqui)" onclick="botFlowBuilder.onPortClick('${node.id}', 'input')"></div>` : ""}
                <div class="flow-node-port output-port" title="Saída (Clique para ligar a outro bloco)" onclick="botFlowBuilder.onPortClick('${node.id}', 'output')"></div>
            `;

            // Node Dragging Logic
            let isDragging = false;
            let startX, startY, initialNodeX, initialNodeY;

            el.addEventListener("mousedown", (e) => {
                if (e.target.classList.contains("flow-node-port")) return;
                this.selectNode(node.id);
                isDragging = true;
                startX = e.clientX;
                startY = e.clientY;
                initialNodeX = node.x;
                initialNodeY = node.y;
                
                const onMouseMove = (ev) => {
                    if (!isDragging) return;
                    const dx = ev.clientX - startX;
                    const dy = ev.clientY - startY;
                    node.x = Math.max(0, initialNodeX + dx);
                    node.y = Math.max(0, initialNodeY + dy);
                    el.style.left = `${node.x}px`;
                    el.style.top = `${node.y}px`;
                    this.renderConnectionsOnly();
                };

                const onMouseUp = () => {
                    isDragging = false;
                    document.removeEventListener("mousemove", onMouseMove);
                    document.removeEventListener("mouseup", onMouseUp);
                };

                document.addEventListener("mousemove", onMouseMove);
                document.addEventListener("mouseup", onMouseUp);
            });

            container.appendChild(el);
        });
    },

    renderConnectionsOnly() {
        const svg = document.getElementById("flow-svg");
        if (!svg) return;
        svg.innerHTML = "";
        this.connections.forEach(conn => {
            const fromNode = this.nodes.find(n => n.id === conn.from);
            const toNode = this.nodes.find(n => n.id === conn.to);
            if (fromNode && toNode) {
                const x1 = fromNode.x + 110;
                const y1 = fromNode.y + 110;
                const x2 = toNode.x + 110;
                const y2 = toNode.y;

                const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
                const d = `M ${x1} ${y1} C ${x1} ${y1 + 60}, ${x2} ${y2 - 60}, ${x2} ${y2}`;
                path.setAttribute("d", d);
                path.setAttribute("stroke", "#6366f1");
                path.setAttribute("stroke-width", "3");
                path.setAttribute("fill", "none");
                path.setAttribute("stroke-dasharray", "6 4");
                path.style.pointerEvents = "all";
                path.addEventListener("click", () => {
                    if (confirm("Remover esta conexão entre os blocos?")) {
                        this.removeConnection(conn.from, conn.to);
                    }
                });
                svg.appendChild(path);
            }
        });
    }
};

window.botFlowBuilder = botFlowBuilder;

