import os
import json
import random
import google.generativeai as genai
import csv # Import for CSV file handling
import io # Import for StringIO

from flask import Flask, request, jsonify, render_template_string

# --- Configuration ---
# Gemini API Key - IMPORTANT: In a real-world scenario, store this in environment variables.
# For Render deployment, you would typically set this in Render's environment variables.
# For this exercise, it's embedded as requested.
GEMINI_API_KEY = "AIzaSyCzx6ReMk8ohPJcCjGwHHzu7SvFccJqAbA" 
GEMINI_MODEL_NAME = "gemini-2.5-flash-preview-05-20"

# Configure Gemini API
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel(GEMINI_MODEL_NAME)

app = Flask(__name__)

# --- Game State ---
game_state = {
    "participants": {},  # {player_name: budget}
    "player_items": {},  # {player_name: [item1, item2]}
    "item_list": [],     # List of items to be auctioned
    "auction_history": [], # List of past events/bids
    "current_item": None,
    "current_bid": 0,
    "high_bidder": None,
    "status": "waiting_for_init", # waiting_for_init, waiting_for_items, bidding, item_sold, game_over, waiting_for_auction_start
    "chat_log": [],      # For displaying chat messages in the UI
    "last_processed_action": None # To prevent reprocessing same action from Gemini
}

# --- Gemini Interaction Functions ---

def generate_gemini_response(user_input, current_game_state_for_gemini):
    """
    Sends user input and current game state to Gemini and expects a narrative
    plus a structured GAME_ACTION if applicable.
    """
    
    # Prepare the context for Gemini
    game_context = f"""
    Current Game State:
    Status: {current_game_state_for_gemini['status']}
    Participants: {json.dumps(current_game_state_for_gemini['participants'])}
    Items Available: {json.dumps(current_game_state_for_gemini['item_list'])}
    Current Item for Auction: {current_game_state_for_gemini['current_item']}
    Current Bid: {current_game_state_for_gemini['current_bid']}
    High Bidder: {current_game_state_for_gemini['high_bidder']}
    """
    
    # Crucial System Prompt for Gemini to follow the rules
    system_prompt = f"""
    You are the Auctioneer for an auction game. Your role is to narrate the auction,
    acknowledge player actions, and guide the game.
    
    When a user provides input that triggers a game state change (like initializing players,
    adding items, placing a bid, or selling an item), you MUST also include a
    'GAME_ACTION:' tag followed by a JSON object on a new line.
    
    The JSON object should have a 'type' field indicating the action and other relevant data.
    
    Here are the expected GAME_ACTION types and their structures:

    1.  **Initialize Game:** When players and a starting budget are set.
        `GAME_ACTION: {{"type": "init_game", "players": ["Player1", "Player2"], "budget": 100}}`

    2.  **Add Items:** When new items are added to the auction list.
        `GAME_ACTION: {{"type": "add_items", "items": ["Item A", "Item B"]}}`

    3.  **Start Item Auction:** When a new item's auction begins.
        `GAME_ACTION: {{"type": "start_item_auction", "item": "Item Name"}}`

    4.  **Place Bid:** When a player makes a bid.
        `GAME_ACTION: {{"type": "bid", "player": "Player Name", "amount": 5}}`
        *Ensure bid is valid (>= current_bid + 1 and within budget)*

    5.  **Sell Item:** When an item is sold to the high bidder.
        `GAME_ACTION: {{"type": "sell_item", "player": "Player Name", "amount": 10, "item": "Item Name"}}`
        *This action should only be triggered after bidding has concluded for an item.*

    6.  **Player Passes:** When a player explicitly passes their turn or passes on an item.
        `GAME_ACTION: {{"type": "pass", "player": "Player Name"}}`

    7.  **No Valid Action:** If user input doesn't correspond to a game action, just provide narrative.
        `GAME_ACTION: {{"type": "no_action"}}`

    You must be clever and intelligent. Always provide a natural language response first,
    then the `GAME_ACTION` if applicable.
    
    Remember the auction rules: Bids start from 1 credit. Budgets decrease after purchase.
    
    Example interaction:
    User: "Start game with John, Jane, Mike, all with 100 credits."
    You: "Welcome, John, Jane, and Mike! Each of you starts with 100 credits. Let the bidding begin!
    GAME_ACTION: {{"type": "init_game", "players": ["John", "Jane", "Mike"], "budget": 100}}"
    
    User: "Add items: Old Vase, Diamond Ring."
    You: "Excellent! We have Old Vase, Diamond Ring ready for auction.
    GAME_ACTION: {{"type": "add_items", "items": ["Old Vase", "Diamond Ring"]}}"
    
    User: "Start auction for the first item."
    You: "Our first item up for bid is the magnificent Old Vase! Who will start us off? Bids begin at 1 credit.
    GAME_ACTION: {{"type": "start_item_auction", "item": "Old Vase"}}"
    
    User: "John bids 5."
    You: "A bold opening bid of 5 credits from John! The current high bid stands at 5. Any other contenders?
    GAME_ACTION: {{"type": "bid", "player": "John", "amount": 5}}"
    
    User: "Sell it!"
    You: "Sold! The Old Vase goes to John for 5 credits! John's new budget is 95.
    GAME_ACTION: {{"type": "sell_item", "player": "John", "amount": 5, "item": "Old Vase"}}"
    
    Strictly adhere to this format. Do not invent other GAME_ACTION types.
    """

    full_prompt = f"{system_prompt}\n\n{game_context}\n\nUser Input: {user_input}"
    
    try:
        response = model.generate_content(full_prompt)
        return response.text
    except Exception as e:
        print(f"Gemini API Error: {e}")
        return f"Auctioneer is currently unavailable due to an error: {e}"

def parse_gemini_response(gemini_text):
    """
    Parses the Gemini response to extract the GAME_ACTION JSON.
    Returns (narrative, action_json_or_None).
    """
    narrative_parts = []
    action_json = None
    
    lines = gemini_text.strip().split('\n')
    for line in lines:
        if line.startswith('GAME_ACTION:'):
            try:
                # Extract JSON string, ensure it's valid
                json_str = line[len('GAME_ACTION:'):].strip()
                # Remove common markdown code block wrappers if present
                if json_str.startswith('```json'):
                    json_str = json_str[len('```json'):]
                if json_str.endswith('```'):
                    json_str = json_str[:-len('```')]
                json_str = json_str.strip() # Remove any extra whitespace
                action_json = json.loads(json_str)
            except json.JSONDecodeError as e:
                print(f"Error parsing GAME_ACTION JSON: {e}\nProblematic string: '{json_str}'")
                action_json = {"type": "parsing_error", "message": str(e), "raw_json": json_str}
            break # Assume only one GAME_ACTION per response
        else:
            narrative_parts.append(line)
            
    narrative = "\n".join(narrative_parts).strip()
    return narrative, action_json

# --- Game Logic Functions ---

def apply_game_action(action):
    """
    Applies a parsed GAME_ACTION to the global game_state.
    """
    global game_state

    action_type = action.get("type")

    # Prevent reprocessing the same action if Gemini repeats it due to context
    action_hash = hash(json.dumps(action, sort_keys=True))
    if action_hash == game_state["last_processed_action"] and action_type != "start_item_auction": # Allow start_item_auction to be called multiple times if items are added incrementally
        print(f"Skipping duplicate action: {action_type}")
        return "Duplicate action skipped."
    game_state["last_processed_action"] = action_hash

    if action_type == "init_game":
        players = action.get("players")
        budget = action.get("budget")
        if not players or not budget:
            return "Error: Missing players or budget for init_game."
        
        game_state["participants"] = {p: budget for p in players}
        game_state["player_items"] = {p: [] for p in players}
        game_state["initial_budget"] = budget # Store initial budget for display
        game_state["status"] = "waiting_for_items"
        game_state["chat_log"].append({"sender": "System", "message": f"Game initialized with players: {', '.join(players)}. Each has {budget} credits."})
        return f"Game initialized for {len(players)} players."

    elif action_type == "add_items":
        items = action.get("items")
        if not items:
            return "Error: No items provided for add_items."
        
        # Ensure items are added to the general list, not just a player's
        # Filter out empty strings from items
        items_to_add = [item.strip() for item in items if item.strip()]
        game_state["item_list"].extend(items_to_add)
        game_state["status"] = "waiting_for_auction_start" if game_state["status"] != "bidding" else game_state["status"]
        game_state["chat_log"].append({"sender": "System", "message": f"Items added: {', '.join(items_to_add)}."})
        
        # If the game is ready and not currently bidding, and there's a current item, start auction if items were just added
        if game_state["status"] == "waiting_for_auction_start" and not game_state["current_item"] and game_state["item_list"]:
            first_item = game_state["item_list"][0]
            # Prompt Gemini to start the auction for the first item
            # This is a bit of a "hack" to get Gemini to issue the "start_item_auction" action
            # for the first item after items are added.
            # Alternatively, we could directly call apply_game_action here, but letting Gemini drive
            # ensures its narrative is aligned.
            game_state["chat_log"].append({"sender": "Auctioneer", "message": f"Excellent, items have been added! Shall we begin the auction for '{first_item}' now?"})
            game_state["chat_log"].append({"sender": "System", "message": "You can now type 'Start auction for the first item.' or 'Start auction for [Item Name]'."})


        return f"Added {len(items_to_add)} items."

    elif action_type == "start_item_auction":
        item_name = action.get("item")
        if not item_name:
            return "Error: Missing item name for start_item_auction."
        
        # Find and set the item. Remove it from the list of available items.
        if item_name in game_state["item_list"]:
            game_state["current_item"] = item_name
            game_state["item_list"].remove(item_name)
            game_state["current_bid"] = 0 # Bids start from 1, but internal current bid can be 0 initially
            game_state["high_bidder"] = None
            game_state["status"] = "bidding"
            game_state["chat_log"].append({"sender": "System", "message": f"Auction for '{item_name}' has started! Current bid: {game_state['current_bid']}"})
            return f"Auction started for '{item_name}'."
        elif game_state["current_item"] == item_name:
            # Item is already being auctioned (e.g., Gemini might re-announce)
            return f"Auction for '{item_name}' is already underway."
        else:
            return f"Error: Item '{item_name}' not found in available items. Available: {', '.join(game_state['item_list'])}"

    elif action_type == "bid":
        player = action.get("player")
        amount = action.get("amount")

        if not player or amount is None:
            return "Error: Missing player or amount for bid."
        if player not in game_state["participants"]:
            return f"Error: Player '{player}' not recognized. Current participants: {', '.join(game_state['participants'].keys())}"
        if amount <= game_state["current_bid"]:
            return f"Error: Bid of {amount} is not higher than current bid of {game_state['current_bid']}. Minimum bid is {game_state['current_bid'] + 1}."
        if amount > game_state["participants"][player]:
            return f"Error: Player '{player}' does not have enough budget ({game_state['participants'][player]}) for a bid of {amount}."
        if game_state["current_item"] is None:
             return "Error: No item is currently being auctioned."
        
        game_state["current_bid"] = amount
        game_state["high_bidder"] = player
        game_state["chat_log"].append({"sender": "System", "message": f"{player} bids {amount} credits for '{game_state['current_item']}'."})
        return f"Bid updated: {player} at {amount}."

    elif action_type == "sell_item":
        # Prioritize current game state for selling if Gemini's info is slightly off
        actual_player = game_state["high_bidder"]
        actual_amount = game_state["current_bid"]
        actual_item = game_state["current_item"]

        if game_state["current_item"] is None:
             return "Error: No item is currently under auction to be sold."

        if not actual_player or actual_amount == 0:
            return f"Error: Cannot sell '{actual_item}' as there is no high bidder or bid is 0."

        if actual_player not in game_state["participants"]:
            return f"Error: Player '{actual_player}' not found to sell item."
        if game_state["participants"][actual_player] < actual_amount:
            return f"Error: Player '{actual_player}' cannot afford {actual_amount} credits for '{actual_item}'."
        
        game_state["participants"][actual_player] -= actual_amount
        game_state["player_items"][actual_player].append(actual_item)
        game_state["auction_history"].append(f"'{actual_item}' sold to {actual_player} for {actual_amount} credits.")
        
        # Add a system message for the sale BEFORE attempting to start the next auction
        sale_message = f"'{actual_item}' sold to {actual_player} for {actual_amount} credits. {actual_player}'s new budget: {game_state['participants'][actual_player]}."
        game_state["chat_log"].append({"sender": "System", "message": sale_message})

        game_state["current_item"] = None
        game_state["current_bid"] = 0
        game_state["high_bidder"] = None
        
        if not game_state["item_list"]:
            game_state["status"] = "game_over"
            game_state["chat_log"].append({"sender": "System", "message": "All items sold! Game Over."})
            return "Game Over: All items sold."
        else:
            # Automatically start auction for the next item
            next_item = game_state["item_list"][0] # Just peek the next item for Gemini's prompt

            # Craft a message to Gemini to prompt it to automatically start the next auction
            auto_prompt_for_gemini = f"The previous item '{actual_item}' was sold. Please start the auction for the next available item, which is '{next_item}'."
            
            # Send this programmatic message to Gemini for its narrative and to trigger the next GAME_ACTION
            gemini_raw_response_for_auto = generate_gemini_response(auto_prompt_for_gemini, {k: v for k, v in game_state.items() if k != "chat_log"})
            narrative_for_auto, game_action_for_auto = parse_gemini_response(gemini_raw_response_for_auto)

            game_state["chat_log"].append({"sender": "Auctioneer", "message": narrative_for_auto})

            if game_action_for_auto and game_action_for_auto.get("type") == "start_item_auction" and game_action_for_auto.get("item") == next_item:
                # If Gemini correctly understood and sent the start_item_auction action for the next item
                # we process it directly. The recursive call will handle state update and add its own System message.
                result_of_auto_start = apply_game_action(game_action_for_auto)
                return f"Item '{actual_item}' sold to {actual_player}. Auction for '{next_item}' automatically started. ({result_of_auto_start})"
            else:
                # Fallback if Gemini doesn't issue the correct action, e.g., if it just narrates.
                # In this case, the user might need to manually tell it to start the next item.
                game_state["status"] = "waiting_for_auction_start" # Set to waiting for explicit start
                game_state["chat_log"].append({"sender": "System", "message": f"Automatic auction start failed for '{next_item}'. Please manually start the auction for it."})
                return f"Item '{actual_item}' sold to {actual_player}. Next item '{next_item}' is pending manual start."


    elif action_type == "pass":
        player = action.get("player")
        if not player or player not in game_state["participants"]:
            return "Error: Player not recognized for passing."
        if game_state["current_item"] is None:
             return "Error: No item currently being auctioned to pass on."
        
        game_state["chat_log"].append({"sender": "System", "message": f"{player} passes on '{game_state['current_item']}'."})
        # The logic for what happens after a pass (e.g., if all pass, item is sold to highest)
        # needs to be inferred by Gemini and then it should issue a 'sell_item' or 'no_action'
        return f"{player} passed."

    elif action_type == "no_action" or action_type == "parsing_error":
        # Gemini just provided narrative or had a parsing error, no state change
        return f"Gemini narrative only or parsing error: {action.get('message', '')}"

    else:
        return f"Unknown game action type: {action_type}"

# --- Flask Routes ---

@app.route('/')
def index():
    """Serves the main HTML page."""
    # The HTML content is embedded directly here
    return render_template_string(HTML_CONTENT)

@app.route('/process_chat', methods=['POST'])
def process_chat():
    """
    Receives user chat input, sends to Gemini, processes response,
    updates game state, and returns new state for UI update.
    """
    user_input = request.json.get('message')
    if not user_input:
        return jsonify({"success": False, "message": "No message provided."}), 400

    game_state["chat_log"].append({"sender": "You", "message": user_input})

    # Prepare a copy of game_state for Gemini, excluding chat_log to avoid prompt bloat
    gemini_friendly_state = {k: v for k, v in game_state.items() if k != "chat_log"}

    gemini_raw_response = generate_gemini_response(user_input, gemini_friendly_state)
    narrative, game_action = parse_gemini_response(gemini_raw_response)

    # Add Gemini's narrative to chat log
    game_state["chat_log"].append({"sender": "Auctioneer", "message": narrative})

    # Apply game action if present
    if game_action:
        action_result = apply_game_action(game_action)
        # Only add a system message if the action wasn't a narrative-only or parsing error
        if game_action.get("type") not in ["no_action", "parsing_error"]:
            game_state["chat_log"].append({"sender": "System", "message": f"Action processed: {action_result}"})
    
    # Return the entire game state for the client to render
    return jsonify({
        "success": True,
        "narrative": narrative,
        "game_state": game_state
    })

@app.route('/upload_items', methods=['POST'])
def upload_items():
    """
    Handles uploading a CSV or TXT file to add items to the auction.
    """
    if 'file' not in request.files:
        return jsonify({"success": False, "message": "No file part in the request."}), 400

    file = request.files['file']

    if file.filename == '':
        return jsonify({"success": False, "message": "No selected file."}), 400

    if file and (file.filename.endswith('.csv') or file.filename.endswith('.txt')):
        try:
            items = []
            file_content = file.read().decode('utf-8').strip()

            if file.filename.endswith('.csv'):
                csv_reader = csv.reader(io.StringIO(file_content))
                for row in csv_reader:
                    if row: # Ensure row is not empty
                        items.append(row[0].strip()) # Assuming item name is in the first column
            else: # .txt file
                items = [line.strip() for line in file_content.splitlines() if line.strip()]

            if not items:
                return jsonify({"success": False, "message": "No valid items found in the file."}), 400
            
            # Apply the game action to add items
            action_result = apply_game_action({"type": "add_items", "items": items})
            
            return jsonify({
                "success": True,
                "message": f"{len(items)} items uploaded successfully.",
                "game_state": game_state # Return updated state
            })

        except Exception as e:
            print(f"File upload error: {e}")
            return jsonify({"success": False, "message": f"Error processing file: {e}"}), 500
    else:
        return jsonify({"success": False, "message": "Invalid file type. Please upload a .csv or .txt file."}), 400


@app.route('/get_game_state', methods=['GET'])
def get_game_state():
    """
    Returns the current full game state. Useful for initial load and periodic updates.
    """
    return jsonify(game_state)

# --- Embedded HTML, CSS, JavaScript ---

HTML_CONTENT = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>AI Auctioneer Game</title>
    <style>
        body {
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
            margin: 0;
            padding: 20px;
            background-color: #f4f7f6;
            color: #333;
            display: flex;
            flex-direction: column;
            align-items: center;
            min-height: 100vh;
        }
        .container {
            display: flex;
            width: 90%;
            max-width: 1200px;
            background-color: #fff;
            box-shadow: 0 4px 10px rgba(0, 0, 0, 0.1);
            border-radius: 10px;
            overflow: hidden; /* Important for containing internal scrollable content */
            margin-bottom: 20px;
            min-height: 650px; /* Set a minimum height for the entire game area */
        }
        .game-info, .chat-interface {
            padding: 25px;
            box-sizing: border-box;
            height: 100%; /* Fill the container's height */
            display: flex; /* Make children flex containers */
            flex-direction: column; /* Stack children vertically */
        }
        .game-info {
            flex: 1; /* Left panel takes 1 part of available width */
            border-right: 1px solid #eee;
            background-color: #fcfcfc;
        }
        .chat-interface {
            flex: 2; /* Right panel takes 2 parts of available width */
            background-color: #fff;
        }
        h1, h2, h3 {
            color: #2c3e50;
            margin-top: 0;
            border-bottom: 2px solid #3498db;
            padding-bottom: 5px;
            margin-bottom: 15px;
            flex-shrink: 0; /* Prevent titles from shrinking */
        }
        .status-message,
        .current-auction-item,
        .item-upload {
            flex-shrink: 0; /* Prevent these sections from shrinking */
            margin-bottom: 15px;
        }
        .current-auction-item {
            background-color: #e8f6f3;
            padding: 15px;
            border-radius: 8px;
            border: 1px solid #d1ede8;
        }
        .current-auction-item p {
            margin: 5px 0;
        }
        
        /* New styling for scrollable list wrappers */
        .scrollable-list-wrapper {
            flex: 1; /* These take up equal remaining space */
            overflow-y: auto; /* Enable vertical scrolling */
            border: 1px solid #e0e0e0;
            border-radius: 8px;
            padding: 10px; /* Inner padding for the scrollable area */
            background-color: #f9f9f9;
            margin-bottom: 15px;
            min-height: 50px; /* Ensure a minimum visible height */
        }
        .scrollable-list-wrapper ul {
            list-style-type: none;
            padding: 0; /* Remove default ul padding */
            margin: 0; /* Remove default ul margin */
        }
        .scrollable-list-wrapper li {
            padding: 8px 0;
            border-bottom: 1px dashed #eee;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        .scrollable-list-wrapper li:last-child {
            border-bottom: none;
        }

        .player-budget {
            font-weight: bold;
            color: #27ae60;
        }
        
        .chat-log {
            flex: 1; /* Allow chat log to take remaining space */
            overflow-y: auto;
            border: 1px solid #e0e0e0;
            border-radius: 8px;
            padding: 15px;
            background-color: #f9f9f9;
            margin-bottom: 15px;
            min-height: 300px; /* Ensure minimum visible height for chat */
            display: flex;
            flex-direction: column;
        }
        .chat-message {
            margin-bottom: 10px;
            line-height: 1.5;
        }
        .chat-message strong {
            color: #3498db;
        }
        .chat-message.You strong {
            color: #e67e22;
        }
        .chat-message.Auctioneer strong {
            color: #9b59b6;
        }
        .chat-message.System strong {
            color: #1abc9c;
        }
        .chat-input {
            display: flex;
            border-top: 1px solid #eee;
            padding-top: 15px;
            flex-shrink: 0; /* Keep input fixed height at bottom */
            margin-top: auto; /* Pushes input to the bottom in a flex column */
        }
        .chat-input input[type="text"] {
            flex: 1;
            padding: 12px;
            border: 1px solid #ccc;
            border-radius: 6px;
            font-size: 1em;
            margin-right: 10px;
        }
        .chat-input button {
            padding: 12px 20px;
            background-color: #3498db;
            color: white;
            border: none;
            border-radius: 6px;
            cursor: pointer;
            font-size: 1em;
            transition: background-color 0.2s;
        }
        .chat-input button:hover {
            background-color: #2980b9;
        }
        .game-over-message {
            text-align: center;
            font-size: 1.5em;
            font-weight: bold;
            color: #e74c3c;
            margin-top: 20px;
        }
        .status-message {
            background-color: #dbe4f0;
            color: #2c3e50;
            padding: 10px;
            border-radius: 5px;
            margin-bottom: 15px;
            font-weight: bold;
            text-align: center;
        }
        .item-upload {
            margin-top: 0; /* Adjusted as flex item */
            padding-top: 20px;
            border-top: 1px solid #eee;
            text-align: center;
        }
        .item-upload input[type="file"] {
            display: block;
            margin: 10px auto;
            padding: 5px;
            border: 1px solid #ccc;
            border-radius: 5px;
            width: 80%;
            max-width: 300px;
        }
        .item-upload button {
            background-color: #27ae60;
            margin-top: 10px;
        }
        .item-upload button:hover {
            background-color: #229954;
        }
        footer {
            margin-top: 20px;
            color: #777;
            font-size: 0.9em;
        }
    </style>
</head>
<body>
    <h1>AI Auctioneer Game</h1>
    <div class="container">
        <div class="game-info">
            <h2>Game Status</h2>
            <div id="status-message" class="status-message">Loading game...</div>

            <div class="current-auction-item" id="current-auction-info">
                <h3>Current Auction</h3>
                <p><strong>Item:</strong> <span id="auction-item">None</span></p>
                <p><strong>Current Bid:</strong> <span id="current-bid">0</span> credits</p>
                <p><strong>High Bidder:</strong> <span id="high-bidder">None</span></p>
            </div>

            <div class="player-list">
                <h3>Participants</h3>
                <div class="scrollable-list-wrapper"> <!-- New wrapper for scrollability -->
                    <ul id="participants-list">
                        <li>No participants yet. Type in chat to start.</li>
                    </ul>
                </div>
            </div>

            <div class="item-list">
                <h3>Items Remaining</h3>
                <div class="scrollable-list-wrapper"> <!-- New wrapper for scrollability -->
                    <ul id="items-remaining-list">
                        <li>No items yet. Type in chat or upload a file to add items.</li>
                    </ul>
                </div>
            </div>
            
            <div class="item-upload">
                <h3>Upload Items from File</h3>
                <input type="file" id="item-file-input" accept=".csv, .txt">
                <button onclick="uploadItems()">Upload Items</button>
                <p><i>(File should contain one item name per line)</i></p>
            </div>

            <div class="auction-history">
                <h3>Auction History</h3>
                <div class="scrollable-list-wrapper"> <!-- New wrapper for scrollability -->
                    <ul id="auction-history-list">
                        <li>No items sold yet.</li>
                    </ul>
                </div>
            </div>
        </div>

        <div class="chat-interface">
            <h2>Auction Chat</h2>
            <div id="chat-log" class="chat-log">
                <!-- Chat messages will be appended here -->
                <div class="chat-message Auctioneer">
                    <strong>Auctioneer:</strong> Welcome to the AI Auctioneer Game!
                    To begin, type: `Start a new auction game with players John, Jane, Mike, and a budget of 100 for everyone.`
                    (Or add your own player names and budget!)
                </div>
            </div>
            <div class="chat-input">
                <input type="text" id="user-message" placeholder="Type your message or bid here...">
                <button onclick="sendMessage()">Send</button>
            </div>
        </div>
    </div>
    <footer>
        Powered by Gemini AI and Flask. Designed for zero human effort deployment.
    </footer>

    <script>
        document.addEventListener('DOMContentLoaded', () => {
            fetchGameState();
            document.getElementById('user-message').addEventListener('keypress', function(event) {
                if (event.key === 'Enter') {
                    sendMessage();
                }
            });
        });

        async function fetchGameState() {
            try {
                const response = await fetch('/get_game_state');
                const data = await response.json();
                updateUI(data);
            } catch (error) {
                console.error('Error fetching game state:', error);
            }
        }

        async function sendMessage() {
            const userMessageInput = document.getElementById('user-message');
            const message = userMessageInput.value.trim();
            if (!message) return;

            userMessageInput.value = ''; // Clear input field

            const chatLog = document.getElementById('chat-log');
            const response = await fetch('/process_chat', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                },
                body: JSON.stringify({ message: message }),
            });
            const data = await response.json();
            
            if (data.success) {
                updateUI(data.game_state);
                chatLog.scrollTop = chatLog.scrollHeight; // Scroll to bottom
            } else {
                console.error('Error processing chat:', data.message);
                const errorMessage = document.createElement('div');
                errorMessage.className = 'chat-message System';
                errorMessage.innerHTML = `<strong>System Error:</strong> ${data.message}`;
                chatLog.appendChild(errorMessage);
                chatLog.scrollTop = chatLog.scrollHeight;
            }
        }

        async function uploadItems() {
            const fileInput = document.getElementById('item-file-input');
            const file = fileInput.files[0];

            if (!file) {
                alert('Please select a file to upload.');
                return;
            }

            const formData = new FormData();
            formData.append('file', file);

            try {
                const response = await fetch('/upload_items', {
                    method: 'POST',
                    body: formData,
                });
                const data = await response.json();

                if (data.success) {
                    updateUI(data.game_state);
                    // Add a chat message about items added from file
                    const chatLog = document.getElementById('chat-log');
                    const systemMessage = document.createElement('div');
                    systemMessage.className = 'chat-message System';
                    systemMessage.innerHTML = `<strong>System:</strong> ${data.message}`;
                    chatLog.appendChild(systemMessage);
                    chatLog.scrollTop = chatLog.scrollHeight;
                } else {
                    alert('Error uploading items: ' + data.message);
                }
            } catch (error) {
                console.error('Error uploading items:', error);
                alert('Network error or server issue during upload.');
            } finally {
                fileInput.value = ''; // Clear the file input
            }
        }

        function updateUI(gameState) {
            // Update Status Message
            const statusMessageDiv = document.getElementById('status-message');
            let statusText = "Game Status: ";
            if (gameState.status === "waiting_for_init") {
                statusText += "Waiting for game initialization (e.g., 'Start game with John, Jane, Mike, budget 100').";
            } else if (gameState.status === "waiting_for_items") {
                statusText += "Game initialized. Waiting for items (e.g., 'Add items: Vase, Ring, Scroll' or upload a file).";
            } else if (gameState.status === "waiting_for_auction_start") {
                statusText += "Items added. Waiting to start auction (e.g., 'Start auction for the first item').";
            } else if (gameState.status === "bidding") {
                statusText += `Auction for '${gameState.current_item}' is active.`;
            } else if (gameState.status === "game_over") {
                statusText += "Game Over - All items sold!";
            }
            statusMessageDiv.textContent = statusText;

            // Update Current Auction Info
            document.getElementById('auction-item').textContent = gameState.current_item || 'None';
            document.getElementById('current-bid').textContent = gameState.current_bid || 0;
            document.getElementById('high-bidder').textContent = gameState.high_bidder || 'None';

            // Update Participants List
            const participantsList = document.getElementById('participants-list');
            participantsList.innerHTML = '';
            if (Object.keys(gameState.participants).length === 0) {
                participantsList.innerHTML = '<li>No participants yet.</li>';
            } else {
                for (const player in gameState.participants) {
                    const li = document.createElement('li');
                    li.innerHTML = `<span>${player}</span> <span class="player-budget">${gameState.participants[player]} credits</span>`;
                    participantsList.appendChild(li);
                }
            }

            // Update Items Remaining List
            const itemsRemainingList = document.getElementById('items-remaining-list');
            itemsRemainingList.innerHTML = '';
            if (gameState.item_list.length === 0 && !gameState.current_item) {
                itemsRemainingList.innerHTML = '<li>No items remaining.</li>';
            } else {
                gameState.item_list.forEach(item => {
                    const li = document.createElement('li');
                    li.textContent = item;
                    itemsRemainingList.appendChild(li);
                });
            }

            // Update Auction History
            const auctionHistoryList = document.getElementById('auction-history-list');
            auctionHistoryList.innerHTML = '';
            if (gameState.auction_history.length === 0) {
                auctionHistoryList.innerHTML = '<li>No items sold yet.</li>';
            } else {
                gameState.auction_history.forEach(entry => {
                    const li = document.createElement('li');
                    li.textContent = entry;
                    auctionHistoryList.appendChild(li);
                });
            }

            // Update Chat Log
            const chatLog = document.getElementById('chat-log');
            // Clear existing log but only append new messages to avoid duplication on refresh
            const existingMessagesCount = chatLog.children.length;
            if (gameState.chat_log.length > existingMessagesCount) {
                for (let i = existingMessagesCount; i < gameState.chat_log.length; i++) {
                    const chatEntry = gameState.chat_log[i];
                    const div = document.createElement('div');
                    div.className = `chat-message ${chatEntry.sender}`;
                    div.innerHTML = `<strong>${chatEntry.sender}:</strong> ${chatEntry.message}`;
                    chatLog.appendChild(div);
                }
                chatLog.scrollTop = chatLog.scrollHeight; // Scroll to the latest message
            }
        }
    </script>
</body>
</html>
"""

if __name__ == '__main__':
    # For Render deployment, Render will provide the PORT env var.
    # It's good practice to get it from there.
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port)
