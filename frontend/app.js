// Q-aura Front-end App logic (Client router & API client)
const API_URL = window.location.port === "3000" ? "http://localhost:8000" : window.location.origin;
let tempContacts = [];

const state = {
    token: localStorage.getItem("qa_token") || null,
    user: JSON.parse(localStorage.getItem("qa_user")) || null,
    tenant_id: localStorage.getItem("qa_tenant_id") || null,
    conversations: [],
    activeConversationId: null,
    ws: null
};

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

// --- WhatsApp Formatting Helper ---
function formatMessageBody(body) {
    if (!body) return "";
    // Safe escape HTML to prevent XSS
    let escaped = body
        .replace(/&/g, "&amp;")
        .replace(/</g, "&lt;")
        .replace(/>/g, "&gt;")
        .replace(/"/g, "&quot;")
        .replace(/'/g, "&#039;");
        
    // Replace *bold* with <strong>bold</strong>
    escaped = escaped.replace(/\*(.*?)\*/g, "<strong>$1</strong>");
    // Replace _italic_ with <em>italic</em>
    escaped = escaped.replace(/_(.*?)_/g, "<em>$1</em>");
    // Replace \n with <br>
    escaped = escaped.replace(/\n/g, "<br>");
    
    return escaped;
}

// --- API Client ---
const api = {
    async post(endpoint, data, useAuth = true) {
        const headers = { "Content-Type": "application/json" };
        if (useAuth && state.token) {
            headers["Authorization"] = `Bearer ${state.token}`;
        }
        const response = await fetch(`${API_URL}${endpoint}`, {
            method: "POST",
            headers,
            body: JSON.stringify(data)
        });
        if (!response.ok) {
            const err = await response.json();
            throw new Error(err.detail || "Erro desconhecido");
        }
        return response.json();
    },

    async get(endpoint) {
        const headers = {};
        if (state.token) {
            headers["Authorization"] = `Bearer ${state.token}`;
        }
        const response = await fetch(`${API_URL}${endpoint}`, { headers });
        if (!response.ok) {
            throw new Error("Erro de conexão");
        }
        return response.json();
    },

    async delete(endpoint) {
        const headers = {};
        if (state.token) {
            headers["Authorization"] = `Bearer ${state.token}`;
        }
        const response = await fetch(`${API_URL}${endpoint}`, {
            method: "DELETE",
            headers
        });
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
            this.loadDashboardMetrics();

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
        }
    },

    async init() {
        if (state.token) {
            try {
                // Busca o perfil real do usuário logado
                const userProfile = await api.get("/api/auth/me");
                state.user = userProfile;
                state.tenant_id = userProfile.tenant_id;
                
                localStorage.setItem("qa_user", JSON.stringify(state.user));
                localStorage.setItem("qa_tenant_id", state.tenant_id);
                
                this.showMainLayout();
                this.connectWebSocket();
                this.updateProfileUI();
                
                // Pré-carrega as configurações da Meta, as Respostas Rápidas e as Métricas
                this.loadMetaSettings();
                this.loadQuickMessages();
                this.loadDashboardMetrics();
            } catch (e) {
                console.error("Erro na autenticação:", e);
                this.logout();
            }
        } else {
            this.navigate("login");
        }
    },

    showMainLayout() {
        document.querySelectorAll(".view").forEach(v => v.classList.remove("active"));
        document.getElementById("main-layout").classList.remove("layout-hidden");
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

    logout() {
        localStorage.clear();
        state.token = null;
        state.user = null;
        state.tenant_id = null;
        if (state.ws) state.ws.close();
        document.getElementById("main-layout").classList.add("layout-hidden");
        this.navigate("login");
    },

    // --- Data Loaders ---
    async loadConversations(status) {
        try {
            const listContainer = document.getElementById("convo-list");
            listContainer.innerHTML = "<p class='subtitle' style='padding: 20px;'>Carregando...</p>";
            
            const activeTab = document.querySelector(".inbox-tabs .tab-btn.active");
            const statusFilter = status || (activeTab ? activeTab.getAttribute("data-status") : "waiting");
            
            const convos = await api.get(`/api/inbox/conversations?status_filter=${statusFilter}`);
            state.conversations = convos;
            
            listContainer.innerHTML = "";
            if (convos.length === 0) {
                listContainer.innerHTML = "<p class='subtitle' style='padding: 20px;'>Nenhuma conversa.</p>";
                return;
            }

            convos.forEach(c => {
                const item = document.createElement("div");
                item.setAttribute("data-id", c.id);
                
                const isUnread = c.unread_count && c.unread_count > 0 && state.activeConversationId !== c.id;
                item.className = `convo-item ${state.activeConversationId === c.id ? 'active' : ''} ${isUnread ? 'unread' : ''}`;
                item.onclick = () => this.selectConversation(c.id);
                
                const contactName = c.contact ? c.contact.name || c.contact.phone_number : "Hóspede";
                
                // Avatar image with DiceBear initials fallback
                const avatarUrl = (c.contact && c.contact.avatar_url)
                    ? c.contact.avatar_url
                    : `https://api.dicebear.com/7.x/initials/svg?seed=${encodeURIComponent(contactName)}`;
                
                // Status label translation (Aguardando, Em atendimento, Finalizado)
                let statusLabel = "";
                if (c.status === "waiting") statusLabel = "Aguardando";
                else if (c.status === "active") statusLabel = "Em atendimento";
                else if (c.status === "resolved") statusLabel = "Finalizado";
                
                const subtitleText = statusLabel;
                
                // Unread badge with number count
                const unreadBadge = isUnread
                    ? `<span class="unread-badge" style="background-color: var(--color-primary); color: white; border-radius: 50%; font-size: 10px; font-weight: 700; width: 18px; height: 18px; display: inline-flex; align-items: center; justify-content: center; margin-left: 8px; box-shadow: 0 0 4px var(--color-primary);">${c.unread_count}</span>`
                    : '';
                
                item.innerHTML = `
                    <img class="avatar" src="${avatarUrl}" alt="${contactName}">
                    <div class="convo-meta">
                        <h4>
                            <span style="display: flex; align-items: center;">
                                ${contactName}
                                ${unreadBadge}
                            </span>
                            <span class="convo-time">Hoje</span>
                        </h4>
                        <p>${subtitleText}</p>
                    </div>
                `;
                listContainer.appendChild(item);
            });
        } catch (e) {
            console.error(e);
        }
    },

    async selectConversation(convoId) {
        state.activeConversationId = convoId;
        
        // Remove unread dots locally on click
        document.querySelectorAll(".convo-item").forEach(item => {
            item.classList.remove("active");
            if (item.getAttribute("data-id") === convoId) {
                item.classList.add("active");
                item.classList.remove("unread");
                const badge = item.querySelector(".unread-badge");
                if (badge) badge.remove();
            }
        });
        
        const convo = state.conversations.find(c => c.id === convoId);
        if (convo) {
            convo.unread = false;
            convo.unread_count = 0;
        }
        
        // Show conversation pane
        const activeArea = document.getElementById("active-chat-area");
        activeArea.classList.remove("empty");
        activeArea.querySelector(".no-chat-selected").style.display = "none";
        activeArea.querySelector(".chat-wrapper").style.display = "flex";

        // Show guest context panel
        document.getElementById("guest-context").style.display = "block";

        // Update contact details in Right panel and Active header
        if (convo && convo.contact) {
            const contactName = convo.contact.name || convo.contact.phone_number || "Hóspede";
            document.getElementById("active-contact-name").innerText = contactName;
            document.getElementById("guest-phone").innerText = convo.contact.phone_number;
            document.getElementById("guest-lang").innerText = convo.contact.language === "pt-BR" ? "Português" : convo.contact.language;
            
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
            document.getElementById("guest-funnel-stage").innerText = stageLabels[stage] || stage.toUpperCase();

            // Set Avatar image
            const avatarUrl = convo.contact.avatar_url || `https://api.dicebear.com/7.x/initials/svg?seed=${encodeURIComponent(contactName)}`;
            const activeAvatar = document.getElementById("active-avatar");
            if (activeAvatar) {
                activeAvatar.innerHTML = `<img class="avatar" src="${avatarUrl}" alt="${contactName}">`;
            }
            
            // Toggle Assumir Atendimento vs Transferir button
            const transferBtn = document.getElementById("btn-transfer-chat");
            if (transferBtn) {
                if (convo.status === "waiting") {
                    transferBtn.innerHTML = `<i class="fa-solid fa-headset"></i> Assumir Atendimento`;
                    transferBtn.classList.remove("btn-secondary");
                    transferBtn.classList.add("btn-primary");
                } else {
                    transferBtn.innerHTML = `<i class="fa-solid fa-arrow-right-arrow-left"></i> Transferir`;
                    transferBtn.classList.remove("btn-primary");
                    transferBtn.classList.add("btn-secondary");
                }
            }
        }

        // Load Messages
        try {
            const messages = await api.get(`/api/inbox/conversations/${convoId}/messages`);
            const scroll = document.getElementById("message-scroll");
            scroll.innerHTML = "";
            
            messages.forEach(m => {
                const bubble = document.createElement("div");
                bubble.className = `message-bubble ${m.sender_type === 'contact' ? 'incoming' : 'outgoing'}`;
                
                if (m.message_type === "image" && m.media_url) {
                    const img = document.createElement("img");
                    
                    let imgSrc = m.media_url;
                    // Se for um ID da Meta, usa o proxy. Se for link do uploader, usa direto.
                    if (!m.media_url.startsWith("http://") && !m.media_url.startsWith("https://")) {
                        imgSrc = `${API_URL}/api/inbox/media/${m.media_url}?token=${state.token}`;
                    }
                    
                    img.src = imgSrc;
                    img.alt = "Imagem";
                    img.className = "chat-media-image";
                    img.onclick = () => window.open(imgSrc, "_blank");
                    bubble.appendChild(img);
                    
                    if (m.body && m.body !== "[Imagem]") {
                        const caption = document.createElement("div");
                        caption.innerHTML = formatMessageBody(m.body);
                        caption.style.marginTop = "8px";
                        bubble.appendChild(caption);
                    }
                } else {
                    bubble.innerHTML = formatMessageBody(m.body);
                }
                scroll.appendChild(bubble);
            });
            scroll.scrollTop = scroll.scrollHeight;
        } catch (e) {
            console.error(e);
        }
    },



    async loadMetaSettings() {
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
            console.log("Credenciais Meta não encontradas ou não configuradas.");
        }
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

    async loadDashboardMetrics() {
        try {
            const metrics = await api.get("/api/inbox/dashboard-metrics");
            
            // Populate Cards
            document.getElementById("stat-conversations").innerText = metrics.total_conversations;
            document.getElementById("stat-bot-resolution").innerText = `${metrics.bot_resolution_rate}%`;
            document.getElementById("stat-frt").innerText = `${metrics.avg_response_time_seconds}s`;
            document.getElementById("stat-conversion").innerText = `${metrics.conversion_rate}%`;
            
            // Populate Funnel
            const funnelContainer = document.getElementById("funnel-container-stats");
            if (funnelContainer && metrics.funnel_stages) {
                funnelContainer.innerHTML = "";
                metrics.funnel_stages.forEach((stage, idx) => {
                    const el = document.createElement("div");
                    el.className = `funnel-stage stage-${idx + 1}`;
                    el.style.width = `${Math.max(stage.percentage, 15)}%`;
                    el.innerHTML = `<span>${stage.stage} (${stage.percentage}%) - ${stage.count} contatos</span>`;
                    funnelContainer.appendChild(el);
                });
            }
            
            // Populate Departments
            const depList = document.getElementById("department-list-stats");
            if (depList && metrics.department_counts) {
                depList.innerHTML = "";
                metrics.department_counts.forEach(dep => {
                    const el = document.createElement("div");
                    el.className = "dep-row";
                    el.innerHTML = `<span>${dep.name}</span><strong>${dep.count}</strong>`;
                    depList.appendChild(el);
                });
            }
        } catch (e) {
            console.error("Erro ao carregar métricas do painel:", e);
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

    connectWebSocket() {
        if (state.ws) state.ws.close();
        
        const wsProtocol = window.location.protocol === "https:" ? "wss:" : "ws:";
        const wsHost = window.location.port === "3000" ? "localhost:8000" : window.location.host;
        state.ws = new WebSocket(`${wsProtocol}//${wsHost}/ws/${state.tenant_id}`);
        
        state.ws.onmessage = (event) => {
            const message = JSON.parse(event.data);
            if (message.type === "new_message" && message.conversation_id === state.activeConversationId) {
                // Append message to scroll area
                const scroll = document.getElementById("message-scroll");
                const bubble = document.createElement("div");
                bubble.className = `message-bubble ${message.sender_type === 'contact' ? 'incoming' : 'outgoing'}`;
                
                if (message.message_type === "image" && message.media_url) {
                    const img = document.createElement("img");
                    
                    let imgSrc = message.media_url;
                    // Se for um ID da Meta, usa o proxy. Se for link do uploader, usa direto.
                    if (!message.media_url.startsWith("http://") && !message.media_url.startsWith("https://")) {
                        imgSrc = `${API_URL}/api/inbox/media/${message.media_url}?token=${state.token}`;
                    }
                    
                    img.src = imgSrc;
                    img.alt = "Imagem";
                    img.className = "chat-media-image";
                    img.onclick = () => window.open(imgSrc, "_blank");
                    bubble.appendChild(img);
                    
                    if (message.body && message.body !== "[Imagem]") {
                        const caption = document.createElement("div");
                        caption.innerHTML = formatMessageBody(message.body);
                        caption.style.marginTop = "8px";
                        bubble.appendChild(caption);
                    }
                } else {
                    bubble.innerHTML = formatMessageBody(message.body);
                }
                scroll.appendChild(bubble);
                scroll.scrollTop = scroll.scrollHeight;
            }
            // Reload list to update last message preview
            this.loadConversations();
        };
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
        showToast("Falha no login: " + err.message, "error");
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

    try {
        await api.post("/api/auth/meta-credentials", {
            phone_number_id,
            waba_id,
            verify_token,
            permanent_access_token
        });
        showToast("Credenciais salvas com sucesso!", "success");
    } catch (err) {
        showToast("Erro ao salvar: " + err.message, "error");
    }
});

// Chat Send Submit
document.getElementById("chat-input-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const input = document.getElementById("chat-message-input");
    const body = input.value.trim();
    if (!body || !state.activeConversationId) return;

    input.value = "";
    try {
        const msg = await api.post(`/api/inbox/send-message?conversation_id=${state.activeConversationId}&body=${encodeURIComponent(body)}`, {});
        
        // Append bubble
        const scroll = document.getElementById("message-scroll");
        const bubble = document.createElement("div");
        bubble.className = "message-bubble outgoing";
        bubble.innerText = msg.body;
        scroll.appendChild(bubble);
        scroll.scrollTop = scroll.scrollHeight;
    } catch (err) {
        showToast("Erro ao enviar: " + err.message, "error");
    }
});

// --- Inbox Tab Filter Click Bindings ---
document.querySelectorAll(".inbox-tabs .tab-btn").forEach(btn => {
    btn.addEventListener("click", async (e) => {
        document.querySelectorAll(".inbox-tabs .tab-btn").forEach(b => b.classList.remove("active"));
        e.currentTarget.classList.add("active");
        const status = e.currentTarget.getAttribute("data-status");
        await appRouter.loadConversations(status);
    });
});

// --- Chat Workspace Actions (Transfer / Resolve) ---
document.getElementById("btn-transfer-chat").addEventListener("click", async () => {
    if (!state.activeConversationId) return;
    
    // Identifica se a conversa atual está na fila (aguardando)
    const convo = state.conversations.find(c => c.id === state.activeConversationId);
    const isWaiting = convo && convo.status === "waiting";
    
    try {
        await api.post(`/api/inbox/conversations/${state.activeConversationId}/assign`, {});
        
        if (isWaiting) {
            showToast("Atendimento assumido! Iniciando conversa...", "success");
            
            // 1. Alterna a aba ativa visualmente para "Minhas" (active)
            document.querySelectorAll(".inbox-tabs .tab-btn").forEach(b => {
                if (b.getAttribute("data-status") === "active") {
                    b.classList.add("active");
                } else {
                    b.classList.remove("active");
                }
            });
            
            // 2. Carrega as conversas da aba "Minhas" (active)
            await appRouter.loadConversations("active");
            
            // 3. Mantém a conversa selecionada e aberta na tela
            await appRouter.selectConversation(state.activeConversationId);
        } else {
            showToast("Conversa transferida com sucesso!", "success");
            
            // Comportamento original para transferência: limpa a tela e recarrega a aba atual
            const activeTab = document.querySelector(".inbox-tabs .tab-btn.active");
            const currentStatus = activeTab ? activeTab.getAttribute("data-status") : "waiting";
            await appRouter.loadConversations(currentStatus);
            
            document.getElementById("active-chat-area").classList.add("empty");
            document.getElementById("active-chat-area").querySelector(".no-chat-selected").style.display = "flex";
            document.getElementById("active-chat-area").querySelector(".chat-wrapper").style.display = "none";
            document.getElementById("guest-context").style.display = "none";
        }
    } catch (err) {
        showToast("Erro ao processar ação: " + err.message, "error");
    }
});

document.getElementById("btn-resolve-chat").addEventListener("click", async () => {
    if (!state.activeConversationId) return;
    try {
        await api.post(`/api/inbox/conversations/${state.activeConversationId}/resolve`, {});
        showToast("Conversa resolvida!", "success");
        
        // Recarrega a fila e limpa a tela de chat ativo
        await appRouter.loadConversations();
        document.getElementById("active-chat-area").classList.add("empty");
        document.getElementById("active-chat-area").querySelector(".no-chat-selected").style.display = "flex";
        document.getElementById("active-chat-area").querySelector(".chat-wrapper").style.display = "none";
        document.getElementById("guest-context").style.display = "none";
    } catch (err) {
        showToast("Erro ao resolver conversa: " + err.message, "error");
    }
});

// --- PMS Simulator Interactive Actions ---
document.getElementById("btn-pms-quote").addEventListener("click", () => {
    const checkinVal = document.getElementById("pms-checkin").value;
    const checkoutVal = document.getElementById("pms-checkout").value;
    if (!checkinVal || !checkoutVal) {
        showToast("Selecione as datas de Check-in e Check-out.", "error");
        return;
    }
    
    const checkin = new Date(checkinVal);
    const checkout = new Date(checkoutVal);
    
    if (checkout <= checkin) {
        showToast("Check-out deve ser após o Check-in.", "error");
        return;
    }
    
    const diffTime = Math.abs(checkout - checkin);
    const diffDays = Math.ceil(diffTime / (1000 * 60 * 60 * 24));
    const dailyRate = 450;
    const total = dailyRate * diffDays;
    
    document.getElementById("pms-quote-total").innerText = `R$ ${total.toLocaleString("pt-BR")}`;
    document.getElementById("pms-quote-result").querySelector("p:nth-child(3)").innerHTML = `<strong>Total:</strong> R$ ${total.toLocaleString("pt-BR")} (${diffDays} diárias)`;
    document.getElementById("pms-quote-result").style.display = "block";
});

document.getElementById("btn-pms-send").addEventListener("click", () => {
    const checkinVal = document.getElementById("pms-checkin").value;
    const checkoutVal = document.getElementById("pms-checkout").value;
    const checkinFormatted = checkinVal.split("-").reverse().join("/");
    const checkoutFormatted = checkoutVal.split("-").reverse().join("/");
    const totalText = document.getElementById("pms-quote-total").innerText;
    
    const msgInput = document.getElementById("chat-message-input");
    msgInput.value = `Olá! Fiz uma simulação de cotação para o período de ${checkinFormatted} a ${checkoutFormatted}. O valor total fica em ${totalText} para a Suíte Standard. Deseja que eu confirme a sua pré-reserva?`;
    msgInput.focus();
    showToast("Texto de cotação copiado para o chat!", "success");
});

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
    if (!file.name.endsWith(".csv")) {
        showToast("Por favor, envie um arquivo contendo uma planilha CSV.", "error");
        return;
    }
    
    const reader = new FileReader();
    reader.onload = function(e) {
        const text = e.target.result;
        parseCSVContacts(text);
    };
    reader.readAsText(file, "UTF-8");
}

function parseCSVContacts(text) {
    const lines = text.split(/\r?\n/);
    if (lines.length <= 1) {
        showToast("O arquivo CSV está vazio ou não possui contatos.", "error");
        return;
    }
    
    tempContacts = [];
    // Tenta detectar colunas pelo cabeçalho
    const headers = lines[0].split(/[;,]/).map(h => h.trim().toLowerCase());
    
    let nameIdx = headers.indexOf("nome");
    let phoneIdx = headers.indexOf("telefone");
    
    // Fallbacks
    if (nameIdx === -1) nameIdx = 0;
    if (phoneIdx === -1) phoneIdx = 1;
    
    for (let i = 1; i < lines.length; i++) {
        const line = lines[i].trim();
        if (!line) continue;
        
        const cols = line.split(/[;,]/).map(c => c.trim().replace(/^["']|["']$/g, ""));
        if (cols.length <= Math.max(nameIdx, phoneIdx)) continue;
        
        const name = cols[nameIdx] || "Hóspede";
        const phone = cols[phoneIdx] || "";
        
        if (phone) {
            tempContacts.push({ name, phone_number: phone });
        }
    }
    
    updateContactsPreview();
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
    const mediaTypeSelect = document.getElementById("campaign-media-type");
    if (!mediaTypeSelect) return; // Prevent run on separate views
    
    const mediaType = mediaTypeSelect.value;
    const mediaUrl = document.getElementById("campaign-media-url").value.trim();
    const body = document.getElementById("campaign-body").value;
    const buttonType = document.getElementById("campaign-button-type").value;
    const btnLabel = document.getElementById("campaign-btn-label").value.trim();
    const btnUrl = document.getElementById("campaign-btn-url").value.trim();
    
    // Exibe ou oculta campo de arquivo de mídia
    const mediaFileGroup = document.getElementById("campaign-media-file-group");
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
    const headerMedia = document.getElementById("preview-header-media");
    const previewImg = document.getElementById("preview-header-img");
    const previewVideo = document.getElementById("preview-header-video");
    const previewAudio = document.getElementById("preview-audio-media");
    
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
    const previewBody = document.getElementById("preview-message-body");
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
    const previewBtn = document.getElementById("preview-action-button");
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
        const mediaType = document.getElementById("campaign-media-type").value;
        const mediaUrl = document.getElementById("campaign-media-url").value.trim();
        const body = document.getElementById("campaign-body").value.trim();
        const buttonType = document.getElementById("campaign-button-type").value;
        const btnLabel = document.getElementById("campaign-btn-label").value.trim();
        const btnUrl = document.getElementById("campaign-btn-url").value.trim();
        
        if (!name) {
            showToast("Por favor, informe o nome da campanha.", "error");
            return;
        }
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
        
        dispatchCampaignBtn.disabled = true;
        dispatchCampaignBtn.innerText = "Agendando disparos...";
        
        try {
            await api.post("/api/inbox/campaigns/send", {
                name,
                media_type: mediaType,
                media_url: mediaType !== "none" ? mediaUrl : null,
                body,
                button_type: buttonType,
                button_label: buttonType !== "none" ? btnLabel : null,
                button_url: buttonType === "cta_url" ? btnUrl : null
            });
            
            showToast("Disparo de campanha iniciado em segundo plano com sucesso!", "success");
            
            // Limpa o formulário
            document.getElementById("campaign-name").value = "";
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
    });
}

if (closeStartChatModalBtn && startChatModal) {
    closeStartChatModalBtn.addEventListener("click", () => {
        startChatModal.style.display = "none";
        startChatForm.reset();
    });
}

if (startChatModal) {
    startChatModal.addEventListener("click", (e) => {
        if (e.target === startChatModal) {
            startChatModal.style.display = "none";
            startChatForm.reset();
        }
    });
}

if (startChatForm) {
    startChatForm.addEventListener("submit", async (e) => {
        e.preventDefault();
        
        const phone = document.getElementById("start-chat-phone").value.trim();
        const name = document.getElementById("start-chat-name").value.trim();
        const body = document.getElementById("start-chat-body").value.trim();
        
        const submitBtn = startChatForm.querySelector("button[type='submit']");
        const originalText = submitBtn.innerText;
        submitBtn.disabled = true;
        submitBtn.innerText = "Iniciando...";
        
        try {
            const messageRes = await api.post("/api/inbox/start-conversation", {
                phone_number: phone,
                body: body,
                name: name || null
            });
            
            showToast("Conversa iniciada com sucesso!", "success");
            startChatModal.style.display = "none";
            startChatForm.reset();
            
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
            } else if (e.key === "ArrowUp") {
                e.preventDefault();
                selectedQuickIndex = (selectedQuickIndex - 1 + filteredQuickReplies.length) % filteredQuickReplies.length;
                updateDropdownSelection();
            } else if (e.key === "Enter") {
                if (selectedQuickIndex >= 0 && selectedQuickIndex < filteredQuickReplies.length) {
                    e.preventDefault();
                    selectQuickReply(filteredQuickReplies[selectedQuickIndex]);
                }
            } else if (e.key === "Escape") {
                qmDropdown.style.display = "none";
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


// Start router
window.appRouter = appRouter;
appRouter.init();

