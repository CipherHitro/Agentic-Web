import streamlit as st
import requests
import os
import threading
from dotenv import load_dotenv

load_dotenv()

st.set_page_config(
    page_title="Agentic Web AI",
    page_icon="🌐",
    layout="wide",
    initial_sidebar_state="expanded",
)

# Config
API_URL = os.getenv("BACKEND_URL", "http://localhost:8000")


@st.cache_data(ttl=120)
def fetch_backend_config():
    """Load deployment mode from backend (development vs production)."""
    try:
        resp = requests.get(f"{API_URL}/config", timeout=5)
        if resp.status_code == 200:
            return resp.json()
    except Exception:
        pass
    return {
        "mode": "development",
        "human_involvement_enabled": True,
        "playwright_headed": True,
    }


backend_config = fetch_backend_config()
human_handoff_enabled = backend_config.get("human_involvement_enabled", True)

AGENT_LOADING_PHASES = [
    "🔍 Searching the web for relevant sources…",
    "🌐 Browsing pages and reading content…",
    "📄 Extracting key information…",
    "🧭 Navigating deeper into websites…",
    "👁️ Observing page layout and controls…",
    "🧠 Planning the next step…",
    "⚡ Putting it all together…",
]

AGENT_LOADING_CSS = """
<style>
@keyframes agent-spin {
  to { transform: rotate(360deg); }
}
@keyframes agent-bounce {
  0%, 80%, 100% { transform: translateY(0); opacity: 0.45; }
  40% { transform: translateY(-7px); opacity: 1; }
}
@keyframes agent-shimmer {
  0% { transform: translateX(-100%); }
  100% { transform: translateX(220%); }
}
@keyframes agent-pulse-ring {
  0% { box-shadow: 0 0 0 0 rgba(59, 130, 246, 0.45); }
  70% { box-shadow: 0 0 0 12px rgba(59, 130, 246, 0); }
  100% { box-shadow: 0 0 0 0 rgba(59, 130, 246, 0); }
}
.agent-loader-card {
  border: 1px solid rgba(59, 130, 246, 0.25);
  border-radius: 14px;
  padding: 1.1rem 1.25rem 1rem;
  margin: 0.4rem 0 0.8rem;
  background: linear-gradient(135deg, rgba(59,130,246,0.08), rgba(99,102,241,0.05));
  animation: agent-pulse-ring 2.2s ease-out infinite;
}
.agent-loader-top {
  display: flex;
  align-items: center;
  gap: 0.85rem;
}
.agent-loader-spinner {
  width: 28px;
  height: 28px;
  border: 3px solid rgba(59, 130, 246, 0.2);
  border-top-color: #3b82f6;
  border-radius: 50%;
  animation: agent-spin 0.9s linear infinite;
  flex-shrink: 0;
}
.agent-loader-title {
  font-size: 1.02rem;
  font-weight: 600;
  margin: 0;
  color: inherit;
}
.agent-loader-phase {
  margin: 0.35rem 0 0.55rem 2.15rem;
  font-size: 0.95rem;
  opacity: 0.92;
  min-height: 1.35rem;
  font-weight: 500;
}
.agent-loader-dots {
  display: inline-flex;
  gap: 5px;
  margin-left: 2.15rem;
}
.agent-loader-dots span {
  width: 7px;
  height: 7px;
  border-radius: 50%;
  background: #3b82f6;
  display: inline-block;
  animation: agent-bounce 1.2s ease-in-out infinite;
}
.agent-loader-dots span:nth-child(2) { animation-delay: 0.15s; }
.agent-loader-dots span:nth-child(3) { animation-delay: 0.3s; }
.agent-loader-track {
  height: 5px;
  border-radius: 999px;
  background: rgba(59, 130, 246, 0.15);
  overflow: hidden;
  margin: 0.65rem 0 0 2.15rem;
}
.agent-loader-track-fill {
  width: 42%;
  height: 100%;
  border-radius: 999px;
  background: linear-gradient(90deg, transparent, #3b82f6, #6366f1, transparent);
  animation: agent-shimmer 1.6s ease-in-out infinite;
}
.agent-loader-hint {
  margin: 0.55rem 0 0 2.15rem;
  font-size: 0.82rem;
  opacity: 0.72;
}
</style>
"""


def render_agent_loading_header() -> None:
    st.markdown(AGENT_LOADING_CSS, unsafe_allow_html=True)
    st.markdown(
        """
        <div class="agent-loader-card">
          <div class="agent-loader-top">
            <div class="agent-loader-spinner"></div>
            <p class="agent-loader-title">Agent is working — browsing the web</p>
          </div>
        """,
        unsafe_allow_html=True,
    )


def render_agent_loading_footer() -> None:
    st.markdown(
        """
          <div class="agent-loader-dots"><span></span><span></span><span></span></div>
          <div class="agent-loader-track"><div class="agent-loader-track-fill"></div></div>
          <p class="agent-loader-hint">
            Complex tasks can take several minutes — please keep this tab open.
          </p>
        </div>
        """,
        unsafe_allow_html=True,
    )


# Thread safety classes
class ThreadResult:
    def __init__(self):
        self.result = None
        self.error = None
        self.done = False


def run_chat_in_thread_safe(api_url, api_messages, container):
    try:
        response = requests.post(
            f"{api_url}/chat",
            json={"messages": api_messages},
            timeout=600,
        )
        if response.status_code == 200:
            container.result = response.json()
        else:
            container.error = f"Backend error: {response.text}"
    except Exception as e:
        container.error = str(e)
    finally:
        container.done = True


def handle_agent_completion(container: ThreadResult) -> None:
    """Apply chat API result to session state after the background thread finishes."""
    st.session_state.agent_running = False
    st.session_state.waiting_for_input = False
    st.session_state.prompt = None

    if container.error:
        st.error(container.error)
        return

    if not container.result:
        return

    result = container.result

    st.session_state.messages.append({
        "role": "assistant",
        "content": result["response"],
        "tool_used": result.get("tool_used"),
        "raw_url": result.get("raw_url"),
        "tool_result": result.get("tool_result"),
        "steps": result.get("steps", []),
    })

    if result.get("new_messages"):
        st.session_state.api_messages.extend(result["new_messages"])
    else:
        st.session_state.api_messages.append({
            "role": "assistant",
            "content": result["response"],
        })

    if st.session_state.messages:
        first_user = next(
            (m["content"] for m in st.session_state.messages if m["role"] == "user"),
            "New Chat",
        )
        session_snapshot = {
            "title": first_user[:40],
            "messages": st.session_state.messages.copy(),
        }
        if (
            not st.session_state.chat_sessions
            or st.session_state.chat_sessions[-1]["messages"] != session_snapshot["messages"]
        ):
            st.session_state.chat_sessions.append(session_snapshot)


@st.fragment(run_every=1.0)
def poll_agent_progress():
    """Poll in a fragment; rotate status text while static CSS animations keep running."""
    if not st.session_state.agent_running:
        return

    phase_idx = st.session_state.get("agent_phase_idx", 0) % len(AGENT_LOADING_PHASES)
    st.session_state.agent_phase_idx = phase_idx + 1
    st.markdown(
        f'<p class="agent-loader-phase">{AGENT_LOADING_PHASES[phase_idx]}</p>',
        unsafe_allow_html=True,
    )

    container = st.session_state.get("thread_container")
    if container and container.done:
        handle_agent_completion(container)
        st.session_state.thread_container = None
        st.rerun()
        return

    if human_handoff_enabled and not st.session_state.waiting_for_input:
        try:
            status_resp = requests.get(f"{API_URL}/human/status", timeout=3)
            if status_resp.status_code == 200:
                status = status_resp.json()
                if status.get("waiting"):
                    st.session_state.waiting_for_input = True
                    st.session_state.prompt = status.get("prompt")
                    st.rerun()
        except Exception:
            pass


# Session state initialization
if "messages" not in st.session_state:
    st.session_state.messages = []

if "api_messages" not in st.session_state:
    st.session_state.api_messages = []

if "agent_running" not in st.session_state:
    st.session_state.agent_running = False

if "chat_result" not in st.session_state:
    st.session_state.chat_result = None

if "chat_error" not in st.session_state:
    st.session_state.chat_error = None

if "waiting_for_input" not in st.session_state:
    st.session_state.waiting_for_input = False

if "prompt" not in st.session_state:
    st.session_state.prompt = None

if "chat_sessions" not in st.session_state:
    st.session_state.chat_sessions = []

if "prefill_prompt" not in st.session_state:
    st.session_state.prefill_prompt = ""


# Sidebar
with st.sidebar:

    st.title("🌐 Agentic Web AI")

    st.markdown("---")

    st.subheader("💡 Example Prompts")

    example_prompts = [
        "Get me the content from https://example.com",
        "What does the Hacker News front page say?",
        "Browse https://news.ycombinator.com and summarize the top stories",
        "Extract the main article from https://blog.example.com/post",
        "Find the latest AI news",
        "Summarize OpenAI recent announcements"
    ]

    for prompt in example_prompts:
        if st.button(prompt, use_container_width=True):
            st.session_state.prefill_prompt = prompt

    st.markdown("---")

    st.subheader("🕘 Conversation History")

    for idx, session in enumerate(reversed(st.session_state.chat_sessions)):
        if st.button(
            session["title"],
            key=f"history_{idx}",
            use_container_width=True
        ):
            st.session_state.messages = session["messages"]
            st.rerun()

    st.markdown("---")

    if backend_config.get("mode") == "production":
        st.warning(
            "Production mode: the agent runs headless. Login, MFA, and CAPTCHA "
            "tasks cannot be handed off to you."
        )
    else:
        st.caption(
            "Development mode: headed browser + human handoff enabled for auth."
        )

    st.info("Powered by OpenRouter + Playwright + FastAPI")


# Main UI
st.title("💬 Chat with Web AI Agent")


# Display messages
for msg in st.session_state.messages:

    with st.chat_message(msg["role"]):

        with st.container(border=True):

            if msg["role"] == "assistant":

                # Copy-supported response block
                st.code(msg["content"], language=None)

                # Tool used
                if msg.get("tool_used"):
                    st.info(f"🛠 Tool Used: {msg['tool_used']}")

                # Execution steps
                if msg.get("steps"):

                    with st.expander(
                        "🛠 Agent Execution Steps",
                        expanded=False
                    ):

                        TOOL_ICONS = {
                             "search_web": "🔍",
                             "browse_web": "🌐",
                             "navigate_page": "🧭",
                             "extract_data": "📄",
                             "click_element": "👆",
                             "fill_form_field": "✍️",
                             "scroll": "↕️",
                             "take_screenshot": "📸",
                             "observe_page": "👁️",
                             "go_back": "⬅️",
                             "finish_task": "🏁",
                         }

                        for step in msg["steps"]:

                            icon = TOOL_ICONS.get(
                                step["tool"],
                                "🛠"
                            )

                            status = (
                                "✅"
                                if step["success"]
                                else "❌"
                            )

                            st.markdown(
                                f"{icon} **{step['tool']}** {status}"
                            )

                # Source link
                if msg.get("raw_url"):
                    st.markdown(
                        f"🔗 **Source:** "
                        f"[{msg['raw_url']}]({msg['raw_url']})"
                    )

            else:
                st.markdown(msg["content"])


# Agent running — animated status (fragment handles motion + polling)
if st.session_state.agent_running:
    render_agent_loading_header()

    if human_handoff_enabled and st.session_state.waiting_for_input:
        st.warning(
            f"⚠️ **Human Input/Action Required:** "
            f"{st.session_state.prompt}"
        )

        with st.form(key="human_input_form", clear_on_submit=True):
            human_ans = st.text_input(
                "Response / Confirmation text",
                placeholder="Type response here (or leave blank and confirm)...",
            )
            submitted_human = st.form_submit_button("Confirm & Resume AI")

            if submitted_human:
                ans = human_ans.strip() if human_ans.strip() else "done"
                try:
                    resp = requests.post(
                        f"{API_URL}/human/response",
                        json={"answer": ans},
                    )
                    if resp.status_code == 200:
                        st.session_state.waiting_for_input = False
                        st.session_state.prompt = None
                        st.success("Submitted! Resuming agent loop...")
                        st.rerun()
                    else:
                        st.error(f"Failed to submit: {resp.text}")
                except Exception as e:
                    st.error(f"Error submitting response: {e}")

    poll_agent_progress()
    render_agent_loading_footer()


# Input area
st.markdown("---")

if not st.session_state.agent_running:

    with st.form(
        key="chat_form",
        clear_on_submit=True
    ):

        user_input = st.text_area(
            "Your message",
            value=st.session_state.prefill_prompt,
            placeholder="Ask me to browse a website...",
            height=80
        )

        cols = st.columns([6, 1])

        with cols[0]:

            submitted = st.form_submit_button(
                "🚀 Send",
                use_container_width=True
            )

        with cols[1]:

            clear_btn = st.form_submit_button(
                "🗑️ Clear",
                use_container_width=True
            )

    # Clear current chat
    if clear_btn:

        st.session_state.messages = []
        st.session_state.api_messages = []
        st.session_state.prefill_prompt = ""

        st.rerun()

    # Submit user input
    if submitted and user_input.strip():

        st.session_state.prefill_prompt = ""

        # Add user message
        st.session_state.messages.append({
            "role": "user",
            "content": user_input
        })

        st.session_state.api_messages.append({
            "role": "user",
            "content": user_input
        })

        # Reset runtime state
        st.session_state.agent_running = True
        st.session_state.chat_result = None
        st.session_state.chat_error = None
        st.session_state.waiting_for_input = False
        st.session_state.prompt = None
        st.session_state.agent_phase_idx = 0

        # Launch background thread
        container = ThreadResult()

        st.session_state.thread_container = container

        thread = threading.Thread(
            target=run_chat_in_thread_safe,
            args=(
                API_URL,
                st.session_state.api_messages,
                container
            )
        )

        thread.start()

        st.rerun()


# Footer
st.markdown("---")

st.caption(
    "Built with ❤️ using FastAPI + "
    "Playwright + Streamlit + OpenRouter"
)

