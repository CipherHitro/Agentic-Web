import streamlit as st
import requests
import os
import threading
import time
from dotenv import load_dotenv

load_dotenv()

# Config
API_URL = os.getenv("BACKEND_URL", "http://localhost:8000")


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
            timeout=600
        )

        if response.status_code == 200:
            container.result = response.json()
        else:
            container.error = f"Backend error: {response.text}"

    except Exception as e:
        container.error = str(e)

    finally:
        container.done = True


# Page setup
st.set_page_config(
    page_title="Agentic Web AI",
    page_icon="🌐",
    layout="wide",
    initial_sidebar_state="expanded"
)

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


# Background thread polling
if st.session_state.agent_running:

    container = st.session_state.get("thread_container")

    if container and container.done:

        st.session_state.agent_running = False
        st.session_state.waiting_for_input = False
        st.session_state.prompt = None

        if container.error:
            st.error(container.error)

        elif container.result:

            result = container.result

            # Add assistant response
            st.session_state.messages.append({
                "role": "assistant",
                "content": result["response"],
                "tool_used": result.get("tool_used"),
                "raw_url": result.get("raw_url"),
                "tool_result": result.get("tool_result"),
                "steps": result.get("steps", [])
            })

            # Update API messages
            if "new_messages" in result and result["new_messages"]:

                st.session_state.api_messages.extend(
                    result["new_messages"]
                )

            else:

                st.session_state.api_messages.append({
                    "role": "assistant",
                    "content": result["response"]
                })

            # Save conversation history
            if st.session_state.messages:

                first_user = next(
                    (
                        m["content"]
                        for m in st.session_state.messages
                        if m["role"] == "user"
                    ),
                    "New Chat"
                )

                session_snapshot = {
                    "title": first_user[:40],
                    "messages": st.session_state.messages.copy()
                }

                if (
                    not st.session_state.chat_sessions
                    or st.session_state.chat_sessions[-1]["messages"]
                    != session_snapshot["messages"]
                ):
                    st.session_state.chat_sessions.append(
                        session_snapshot
                    )

            # Raw scraped data viewer
            if (
                result.get("tool_result")
                and result["tool_result"].get("success")
            ):

                with st.expander(
                    "📄 Raw Scraped Data",
                    expanded=False
                ):

                    tool_res = result["tool_result"]

                    st.markdown(
                        f"**Title:** "
                        f"{tool_res.get('title', 'N/A')}"
                    )

                    st.markdown(
                        f"**URL:** "
                        f"{tool_res.get('url', 'N/A')}"
                    )

                    if tool_res.get("links"):

                        st.subheader("🔗 Links Found")

                        for link in tool_res["links"][:10]:

                            st.markdown(
                                f"- [{link.get('text', 'Link')}]"
                                f"({link.get('url')})"
                            )

                    if tool_res.get("content"):

                        st.subheader("📝 Raw Content")

                        st.text_area(
                            "Content",
                            tool_res["content"],
                            height=300
                        )

        st.session_state.thread_container = None

        st.rerun()

    else:

        # Waiting for human intervention
        if st.session_state.waiting_for_input:

            st.warning(
                f"⚠️ **Human Input/Action Required:** "
                f"{st.session_state.prompt}"
            )

            with st.form(
                key="human_input_form",
                clear_on_submit=True
            ):

                human_ans = st.text_input(
                    "Response / Confirmation text",
                    placeholder=(
                        "Type response here "
                        "(or leave blank and confirm)..."
                    )
                )

                submitted_human = st.form_submit_button(
                    "Confirm & Resume AI"
                )

                if submitted_human:

                    ans = (
                        human_ans.strip()
                        if human_ans.strip()
                        else "done"
                    )

                    try:

                        resp = requests.post(
                            f"{API_URL}/human/response",
                            json={"answer": ans}
                        )

                        if resp.status_code == 200:

                            st.session_state.waiting_for_input = False
                            st.session_state.prompt = None

                            st.success(
                                "Submitted! Resuming agent loop..."
                            )

                            st.rerun()

                        else:
                            st.error(
                                f"Failed to submit: {resp.text}"
                            )

                    except Exception as e:
                        st.error(
                            f"Error submitting response: {e}"
                        )

        else:

            with st.spinner(
                "🤖 AI is thinking... "
                "(may browse the web)"
            ):

                try:

                    status_resp = requests.get(
                        f"{API_URL}/human/status"
                    )

                    if status_resp.status_code == 200:

                        status = status_resp.json()

                        if status.get("waiting"):

                            st.session_state.waiting_for_input = True
                            st.session_state.prompt = status["prompt"]

                            st.rerun()

                except Exception:
                    pass

                time.sleep(1.5)

                st.rerun()


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

else:

    st.info(
        "Agent is currently executing a task. "
        "Please wait or respond to the handoff request above."
    )


# Footer
st.markdown("---")

st.caption(
    "Built with ❤️ using FastAPI + "
    "Playwright + Streamlit + OpenRouter"
)

