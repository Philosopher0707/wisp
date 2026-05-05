#!/usr/bin/env python3
"""Example: Basic SDK usage with the high-level Wisp class.

Run:
    python examples/sdk_basic.py

This demonstrates the simplest way to use Wisp programmatically.
"""

from wisp import Wisp


def main():
    # Create an agent instance
    agent = Wisp(
        model="llama3.2",
        workspace=".",
        auto_approve=False,  # Prompt before dangerous commands
        show_thinking=True,  # Show model reasoning
    )

    # Run a prompt and consume events
    print("🤖 Agent is thinking...\n")
    for event in agent.run("List all Python files in the current directory"):
        # Handle different event types
        if event.type == "content":
            print(event.text, end="", flush=True)
        elif event.type == "thinking":
            print(f"\n💭 {event.text}")
        elif event.type == "tool_call":
            print(f"\n🔧 Tool: {event.data['name']}({event.data['arguments']})")
        elif event.type == "tool_result":
            print(f"\n📊 Result: {event.data['result'][:200]}...")
        elif event.type == "error":
            print(f"\n❌ Error: {event.data['message']}")
        elif event.type == "done":
            print(f"\n\n✅ Done! Session: {event.data['session_id']}")


if __name__ == "__main__":
    main()
