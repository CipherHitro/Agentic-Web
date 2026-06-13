import streamlit as st
import requests
import os
from dotenv import load_dotenv

load_dotenv()

# Config
API_URL = os.getenv("BACKEND_URL", "http://localhost:8000")

# Page setup
st.set_page_config(
    page_title="Agentic Web AI",
    page_icon="🌐",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Initialize session state
if "messages" not in st.session_state:
    st.session_state.messages = []

if "api_messages" not in st.session_state:
    st.session_state.api_messages = []

# Sidebar
with st.sidebar:
    st.title("🌐 Agentic Web AI")
    st.markdown("---")
    
    st.subheader("💡 Example Prompts")
    st.markdown("""
    - *"Get me the content from https://example.com"*
    - *"What does the Hacker News front page say?"*
    - *"Browse https://news.ycombinator.com and summarize the top stories"*
    - *"Extract the main article from https://blog.example.com/post"*
    """)
    
    st.markdown("---")
    st.info("Powered by OpenRouter + Playwright + FastAPI")

# Main chat interface
st.title("💬 Chat with Web AI Agent")

# Display chat history
for msg in st.session_state.messages:
    with st.chat_message(msg["role"]):
        if msg["role"] == "assistant" and msg.get("tool_used"):
            st.markdown(f"🔧 **Tool Used:** `{msg['tool_used']}`")
        
        st.markdown(msg["content"])
        
        if msg["role"] == "assistant" and msg.get("raw_url"):
            st.markdown(f"🔗 **Source:** [{msg['raw_url']}]({msg['raw_url']})")

# Input area
st.markdown("---")
with st.form(key="chat_form", clear_on_submit=True):
    user_input = st.text_area(
        "Your message",
        placeholder="Ask me to browse a website...",
        height=80
    )
    cols = st.columns([6, 1])
    with cols[0]:
        submitted = st.form_submit_button("🚀 Send", use_container_width=True)
    with cols[1]:
        clear_btn = st.form_submit_button("🗑️ Clear", use_container_width=True)

if clear_btn:
    st.session_state.messages = []
    st.session_state.api_messages = []
    st.rerun()

if submitted and user_input.strip():
    # Add user message
    st.session_state.messages.append({
        "role": "user",
        "content": user_input
    })
    st.session_state.api_messages.append({
        "role": "user",
        "content": user_input
    })
    
    # Show spinner while AI thinks
    with st.spinner("🤖 AI is thinking... (may browse the web)"):
        try:
            # Call FastAPI backend
            response = requests.post(
                f"{API_URL}/chat",
                json={"messages": st.session_state.api_messages},
                timeout=600  # Browsing can easily take 3-5+ minutes for complex tasks
            )
            
            if response.status_code == 200:
                result = response.json()
                
                # Add AI response to history
                st.session_state.messages.append({
                    "role": "assistant",
                    "content": result["response"],
                    "tool_used": result.get("tool_used"),
                    "raw_url": result.get("raw_url"),
                    "tool_result": result.get("tool_result")
                })
                
                # Update api messages with returned new_messages
                if "new_messages" in result and result["new_messages"]:
                    st.session_state.api_messages.extend(result["new_messages"])
                else:
                    st.session_state.api_messages.append({
                        "role": "assistant",
                        "content": result["response"]
                    })
                
                # If tool was used, show raw data in expander
                if result.get("tool_result") and result["tool_result"].get("success"):
                    with st.expander("📄 Raw Scraped Data"):
                        tool_res = result["tool_result"]
                        st.markdown(f"**Title:** {tool_res.get('title', 'N/A')}")
                        st.markdown(f"**URL:** {tool_res.get('url', 'N/A')}")
                        
                        if tool_res.get("links"):
                            st.subheader("🔗 Links Found")
                            for link in tool_res["links"][:10]:
                                st.markdown(f"- [{link.get('text', 'Link')}]({link.get('url')})")
                        
                        if tool_res.get("content"):
                            st.subheader("📝 Raw Content")
                            st.text_area("Content", tool_res["content"], height=300)
            else:
                st.error(f"Backend error: {response.text}")
                
        except Exception as e:
            st.error(f"Error: {str(e)}")
    
    st.rerun()

# Footer
st.markdown("---")
st.caption("Built with ❤️ using FastAPI + Playwright + Streamlit + OpenRouter")