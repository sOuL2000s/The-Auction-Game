import os
import json
import random
import re # Import for regular expressions
import csv
import io

from flask import Flask, request, jsonify, render_template_string

# --- Configuration ---
# Removed GEMINI_API_KEY and GEMINI_MODEL_NAME as AI is no longer used.

app = Flask(__name__)

# --- Game State ---
# Define a function to get the initial game state, allowing for easy reset
def get_initial_game_state():
    return {
        "participants": {},  # {player_name: budget}
        "player_items": {},  # {player_name: [{"name": item_name, "price": price}]}
        "item_list": [],     # List of items to be auctioned
        "auction_history": [], # List of past events/bids (e.g., "ItemX sold to PlayerY for Z credits")
        "current_item": None,
        "current_bid": 0,
        "high_bidder": None,
        "status": "waiting_for_init", # waiting_for_init, waiting_for_items, waiting_for_auction_start, bidding, game_over
        "chat_log": [{"sender": "Auctioneer", "message": "Welcome to the Rule-Based Auctioneer Game! To begin, type: `Start a new auction game with players John, Jane, Mike, and a budget of 100 for everyone.` (Or add your own player names and budget!)"}],      # For displaying chat messages in the UI
        "last_processed_action_hash": None # To prevent reprocessing same action from simple parsing
    }

game_state = get_initial_game_state()

# --- Rule-Based Command Processing ---

def process_user_command(user_input, current_game_state_for_logic):
    """
    Parses user input using rule-based logic to determine game actions and narratives.
    Returns (narrative, game_action).
    """
    narrative = ""
    game_action = {"type": "no_action"}
    
    user_input_lower = user_input.lower().strip()

    # 1. Initialize Game
    match = re.match(r"start a new auction game with players ([\w,\s]+) and a budget of (\d+)(?: for everyone)?\.?", user_input_lower)
    if match and current_game_state_for_logic["status"] == "waiting_for_init":
        player_names = [p.strip() for p in match.group(1).split(',') if p.strip()]
        budget = int(match.group(2))
        if player_names and budget > 0:
            narrative = f"Welcome, {', '.join(player_names)}! Each of you starts with {budget} credits. Let the game begin!"
            game_action = {"type": "init_game", "players": player_names, "budget": budget}
            return narrative, game_action
        else:
            narrative = "Invalid players or budget specified for starting the game."
            return narrative, {"type": "no_action"}

    # 2. Add Items
    match = re.match(r"add items: (.*)", user_input_lower)
    if match and current_game_state_for_logic["status"] in ["waiting_for_items", "waiting_for_auction_start", "game_over"]: # Allow adding items at various stages
        items_str = match.group(1)
        items = [item.strip() for item in items_str.split(',') if item.strip()]
        if items:
            narrative = f"Excellent! We have {', '.join(items)} ready for auction."
            game_action = {"type": "add_items", "items": items}
            return narrative, game_action
        else:
            narrative = "No items specified to add."
            return narrative, {"type": "no_action"}

    # 3. Start Item Auction (by name or "first")
    match = re.match(r"start auction for (?:the first item|([a-zA-Z0-9\s]+))\b\.?", user_input_lower)
    if match and current_game_state_for_logic["status"] in ["waiting_for_auction_start", "item_sold"] and current_game_state_for_logic["item_list"]:
        item_name = match.group(1).strip() if match.group(1) else current_game_state_for_logic["item_list"][0]
        
        if item_name == current_game_state_for_logic["current_item"]:
            narrative = f"Auction for '{item_name}' is already underway! What's your bid?"
            return narrative, {"type": "no_action"}

        if item_name in current_game_state_for_logic["item_list"]:
            narrative = f"Our next item up for bid is the magnificent '{item_name}'! Who will start us off? Bids begin at 1 credit."
            game_action = {"type": "start_item_auction", "item": item_name}
            return narrative, game_action
        else:
            narrative = f"Item '{item_name}' not found in the list of available items. Available: {', '.join(current_game_state_for_logic['item_list'])}"
            return narrative, {"type": "no_action"}
    elif match and not current_game_state_for_logic["item_list"]:
        narrative = "There are no items available to auction yet. Please add some first!"
        return narrative, {"type": "no_action"}
    elif match and current_game_state_for_logic["status"] == "bidding":
        narrative = f"An auction for '{current_game_state_for_logic['current_item']}' is already in progress. Please bid or sell it first."
        return narrative, {"type": "no_action"}

    # 4. Place Bid
    match = re.match(r"([\w\s]+) bids (\d+)\.?$", user_input_lower)
    if match and current_game_state_for_logic["status"] == "bidding":
        player_name = match.group(1).strip().title() # Capitalize for consistency
        amount = int(match.group(2))

        if player_name not in current_game_state_for_logic["participants"]:
            narrative = f"Player '{player_name}' is not a recognized participant. Please ensure the player name is correct."
            return narrative, {"type": "no_action"}
        if amount <= current_game_state_for_logic["current_bid"]:
            narrative = f"That bid is not high enough. The current bid for '{current_game_state_for_logic['current_item']}' is {current_game_state_for_logic['current_bid']}. Please bid at least {current_game_state_for_logic['current_bid'] + 1}."
            return narrative, {"type": "no_action"}
        if amount > current_game_state_for_logic["participants"][player_name]:
            narrative = f"'{player_name}' cannot afford a bid of {amount} credits. They only have {current_game_state_for_logic['participants'][player_name]} credits remaining."
            return narrative, {"type": "no_action"}
        
        narrative = f"A bold bid of {amount} credits from {player_name}! The current high bid for '{current_game_state_for_logic['current_item']}' stands at {amount}. Any other contenders?"
        game_action = {"type": "bid", "player": player_name, "amount": amount}
        return narrative, game_action
    elif match and current_game_state_for_logic["status"] != "bidding":
        narrative = "No item is currently under auction. You need to start an auction first."
        return narrative, {"type": "no_action"}

    # 5. Sell Item (Implicit or Explicit)
    match = re.match(r"sell (it|item|the item)(?: to ([\w\s]+) for (\d+))?\.?", user_input_lower)
    if match and current_game_state_for_logic["current_item"]:
        target_player = match.group(2)
        target_amount = match.group(3)

        if target_player and target_amount: # Explicit sale to a player for a specified amount
            player_name = target_player.strip().title()
            amount = int(target_amount)
            if player_name not in current_game_state_for_logic["participants"]:
                narrative = f"Player '{player_name}' not recognized. Cannot sell item."
                return narrative, {"type": "no_action"}
            if amount > current_game_state_for_logic["participants"][player_name]:
                narrative = f"'{player_name}' cannot afford {amount} credits for '{current_game_state_for_logic['current_item']}'. Sale cancelled."
                return narrative, {"type": "no_action"}
            
            narrative = f"Sold! The '{current_game_state_for_logic['current_item']}' goes to {player_name} for {amount} credits!"
            game_action = {"type": "sell_item", "item": current_game_state_for_logic["current_item"], "player": player_name, "amount": amount}
            return narrative, game_action

        else: # Implicit sale (to high bidder or unsold)
            if current_game_state_for_logic["high_bidder"] and current_game_state_for_logic["current_bid"] > 0:
                player_name = current_game_state_for_logic["high_bidder"]
                amount = current_game_state_for_logic["current_bid"]
                narrative = f"Sold! The '{current_game_state_for_logic['current_item']}' goes to {player_name} for {amount} credits!"
                game_action = {"type": "sell_item", "item": current_game_state_for_logic["current_item"], "player": player_name, "amount": amount}
            else:
                narrative = f"As there are no valid bids on the '{current_game_state_for_logic['current_item']}', it remains unsold for now. Perhaps it will return later, or we move on."
                game_action = {"type": "sell_item", "item": current_game_state_for_logic["current_item"], "player": None, "amount": 0}
            return narrative, game_action
    elif match and not current_game_state_for_logic["current_item"]:
        narrative = "There is no item currently under auction to sell."
        return narrative, {"type": "no_action"}

    # 6. Player Passes
    match = re.match(r"([\w\s]+) passes\.?", user_input_lower)
    if match and current_game_state_for_logic["current_item"]:
        player_name = match.group(1).strip().title()
        if player_name not in current_game_state_for_logic["participants"]:
            narrative = f"Player '{player_name}' not recognized."
            return narrative, {"type": "no_action"}
        narrative = f"{player_name} passes on '{current_game_state_for_logic['current_item']}'. Any other bids?"
        game_action = {"type": "pass", "player": player_name} # This action might not trigger a state change directly, but records the pass.
        return narrative, game_action
    elif match and not current_game_state_for_logic["current_item"]:
        narrative = "No item is currently under auction to pass on."
        return narrative, {"type": "no_action"}

    # If no specific command is matched
    return f"Auctioneer: I didn't understand your command: '{user_input}'. Please try again with a clear instruction (e.g., 'John bids 10' or 'Sell it!').", {"type": "no_action"}


# --- Game Logic Functions ---

def apply_game_action(action):
    """
    Applies a parsed GAME_ACTION to the global game_state.
    """
    global game_state

    action_type = action.get("type")

    # Simple hash to detect direct action repetitions from UI buttons,
    # but still allow complex chat commands that *might* lead to the same state
    # or follow-up actions like starting a new auction.
    action_hash = hash(json.dumps(action, sort_keys=True))
    if action_hash == game_state["last_processed_action_hash"] and action_type not in ["pass", "no_action"]:
        # Only prevent exact, non-narrative-only actions from being reprocessed immediately.
        # Pass/no_action can be repeated without major state changes, and
        # start_item_auction might be triggered multiple times if the user clicks the button.
        print(f"Skipping duplicate action: {action_type}")
        return "Duplicate action skipped."
    game_state["last_processed_action_hash"] = action_hash


    if action_type == "init_game":
        players = action.get("players")
        budget = action.get("budget")
        if not players or budget is None:
            return "Error: Missing players or budget for init_game."
        
        game_state["participants"] = {p.title(): budget for p in players} # Ensure consistent capitalization
        game_state["player_items"] = {p.title(): [] for p in players}
        game_state["initial_budget"] = budget # Store initial budget for display
        game_state["status"] = "waiting_for_items"
        game_state["chat_log"].append({"sender": "System", "message": f"Game initialized with players: {', '.join(game_state['participants'].keys())}. Each has {budget} credits."})
        return f"Game initialized for {len(players)} players."

    elif action_type == "add_items":
        items = action.get("items")
        if not items:
            return "Error: No items provided for add_items."
        
        items_to_add = [item.strip().title() for item in items if item.strip()] # Capitalize items
        game_state["item_list"].extend(items_to_add)
        # Update status based on current game flow
        if game_state["status"] == "waiting_for_items":
            game_state["status"] = "waiting_for_auction_start"
            game_state["chat_log"].append({"sender": "Auctioneer", "message": f"Excellent, items have been added! You can now use the 'Start Next Auction' button or type 'Start auction for the first item'."})
        game_state["chat_log"].append({"sender": "System", "message": f"Items added: {', '.join(items_to_add)}."})
        return f"Added {len(items_to_add)} items."

    elif action_type == "start_item_auction":
        item_name = action.get("item")
        if not item_name:
            return "Error: Missing item name for start_item_auction."
        
        # Ensure we're not starting an auction for the same item twice if it's already active
        if game_state["current_item"] == item_name and game_state["status"] == "bidding":
            return f"Auction for '{item_name}' is already underway."
        
        if item_name in game_state["item_list"]:
            game_state["current_item"] = item_name
            game_state["current_bid"] = 0 
            game_state["high_bidder"] = None
            game_state["status"] = "bidding"
            game_state["chat_log"].append({"sender": "System", "message": f"Auction for '{item_name}' has started! Current bid: {game_state['current_bid']}"})
            return f"Auction started for '{item_name}'."
        else:
            return f"Error: Item '{item_name}' not found in available items or already auctioned."

    elif action_type == "bid":
        player = action.get("player").title() # Ensure consistent capitalization
        amount = action.get("amount")

        if not player or amount is None:
            return "Error: Missing player or amount for bid."
        if player not in game_state["participants"]:
            return f"Error: Player '{player}' not recognized."
        if game_state["current_item"] is None:
             return "Error: No item is currently being auctioned to bid on."
        if amount <= game_state["current_bid"]:
            return f"Error: Bid of {amount} is not higher than current bid of {game_state['current_bid']}. Minimum bid is {game_state['current_bid'] + 1}."
        if amount > game_state["participants"][player]:
            return f"Error: Player '{player}' does not have enough budget ({game_state['participants'][player]}) for a bid of {amount}."
        
        game_state["current_bid"] = amount
        game_state["high_bidder"] = player
        game_state["chat_log"].append({"sender": "System", "message": f"{player} bids {amount} credits for '{game_state['current_item']}'."})
        return f"Bid updated: {player} at {amount}."

    elif action_type == "sell_item":
        item_to_sell = action.get("item")
        actual_player = action.get("player") # This will be None if unsold
        actual_amount = action.get("amount")

        if item_to_sell is None:
             return "Error: No item is currently under auction to be sold or declared unsold."

        # Remove item from the general list
        if item_to_sell in game_state["item_list"]:
            game_state["item_list"].remove(item_to_sell)
        
        if actual_player and actual_player in game_state["participants"] and actual_amount > 0:
            # Valid sale
            if game_state["participants"][actual_player] < actual_amount:
                # Should be caught by bid validation but a final check
                sale_message = f"Error: Player '{actual_player}' cannot afford {actual_amount} credits for '{item_to_sell}'. Item declared unsold due to affordability."
                game_state["chat_log"].append({"sender": "System", "message": sale_message})
                game_state["auction_history"].append(f"'{item_to_sell}' was declared UNSOLD (affordability issue).")
            else:
                game_state["participants"][actual_player] -= actual_amount
                # Store item with price
                game_state["player_items"][actual_player].append({"name": item_to_sell, "price": actual_amount})
                game_state["auction_history"].append(f"'{item_to_sell}' sold to {actual_player} for {actual_amount} credits.")
                sale_message = f"'{item_to_sell}' sold to {actual_player} for {actual_amount} credits. {actual_player}'s new budget: {game_state['participants'][actual_player]}."
                game_state["chat_log"].append({"sender": "System", "message": sale_message})
        else:
            # Item is declared unsold (no bids, or player cannot afford)
            game_state["auction_history"].append(f"'{item_to_sell}' was declared UNSOLD (no valid bids).")
            game_state["chat_log"].append({"sender": "System", "message": f"'{item_to_sell}' declared UNSOLD. No valid bids were received."})
        
        # Reset current auction state
        game_state["current_item"] = None
        game_state["current_bid"] = 0
        game_state["high_bidder"] = None
        
        if not game_state["item_list"]:
            game_state["status"] = "game_over"
            game_state["chat_log"].append({"sender": "System", "message": "All items sold or declared unsold! Game Over. Reset the game to play again."})
            return "Game Over: All items processed."
        else:
            game_state["status"] = "waiting_for_auction_start"
            next_item = game_state["item_list"][0]
            game_state["chat_log"].append({"sender": "System", "message": f"'{item_to_sell}' processed. The next item is '{next_item}'. Use the 'Start Next Auction' button or type 'Start auction for {next_item}'."})
            return f"Item '{item_to_sell}' processed. Next item '{next_item}' is pending start."

    elif action_type == "pass":
        player = action.get("player")
        # No direct state change here, just acknowledge the pass.
        # The 'sell_item' action (explicit or implicit) determines what happens after passes.
        game_state["chat_log"].append({"sender": "System", "message": f"{player} passes on '{game_state['current_item']}'."})
        return f"{player} passed."

    elif action_type == "no_action":
        return "No specific game action identified from your input."

    else:
        return f"Unknown game action type: {action_type}"

# --- Flask Routes ---

@app.route('/')
def index():
    """Serves the main HTML page."""
    return render_template_string(HTML_CONTENT)

@app.route('/process_chat', methods=['POST'])
def process_chat():
    """
    Receives user chat input, processes it using rule-based logic,
    updates game state, and returns new state for UI update.
    """
    user_input = request.json.get('message')
    if not user_input:
        return jsonify({"success": False, "message": "No message provided."}), 400

    game_state["chat_log"].append({"sender": "You", "message": user_input})

    # Use a copy of the game state for parsing to avoid side effects during parsing itself
    narrative, game_action = process_user_command(user_input, {k: v for k, v in game_state.items() if k != "chat_log"})

    # Add narrative from rule-based system
    game_state["chat_log"].append({"sender": "Auctioneer", "message": narrative})

    if game_action and game_action.get("type") != "no_action":
        action_result = apply_game_action(game_action)
        # Only add a system message if it was a real action, not just a narrative
        if not action_result.startswith("Duplicate action skipped"):
            game_state["chat_log"].append({"sender": "System", "message": f"Action processed: {action_result}"})
    
    return jsonify({
        "success": True,
        "narrative": narrative, # Frontend can still use this for confirmation, but chat_log is primary
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
            
            action_result = apply_game_action({"type": "add_items", "items": items})
            
            return jsonify({
                "success": True,
                "message": f"{len(items)} items uploaded successfully.",
                "game_state": game_state 
            })

        except Exception as e:
            print(f"File upload error: {e}")
            return jsonify({"success": False, "message": f"Error processing file: {e}"}), 500
    else:
        return jsonify({"success": False, "message": "Invalid file type. Please upload a .csv or .txt file."}), 400

@app.route('/reset_game', methods=['POST'])
def reset_game():
    """
    Resets the entire game state to its initial values.
    """
    global game_state
    game_state = get_initial_game_state()
    game_state["chat_log"].append({"sender": "System", "message": "Game has been reset. Start a new auction!"})
    return jsonify({
        "success": True,
        "message": "Game state reset.",
        "game_state": game_state
    })

@app.route('/start_next_auction_action', methods=['POST'])
def start_next_auction_action():
    """
    Initiates an auction for the next available item via a button click.
    """
    global game_state
    if game_state["item_list"] and game_state["status"] in ["waiting_for_auction_start", "item_sold", "game_over"]:
        item_to_start = game_state["item_list"][0]
        narrative = f"Auctioneer: The auction for '{item_to_start}' is now open! Bids begin at 1 credit."
        game_action = {"type": "start_item_auction", "item": item_to_start}
        
        game_state["chat_log"].append({"sender": "Auctioneer", "message": narrative})
        action_result = apply_game_action(game_action)
        game_state["chat_log"].append({"sender": "System", "message": f"Action processed: {action_result}"})
        
        return jsonify({"success": True, "game_state": game_state})
    elif game_state["status"] == "bidding":
        narrative = f"Auctioneer: An auction for '{game_state['current_item']}' is already in progress. Please bid or sell it first."
        game_state["chat_log"].append({"sender": "Auctioneer", "message": narrative})
        return jsonify({"success": False, "message": narrative, "game_state": game_state}), 400
    else:
        narrative = "Auctioneer: No items available to start an auction, or game not ready. Please add items first, or initialize the game."
        game_state["chat_log"].append({"sender": "Auctioneer", "message": narrative})
        return jsonify({"success": False, "message": narrative, "game_state": game_state}), 400

@app.route('/sell_current_item_action', methods=['POST'])
def sell_current_item_action():
    """
    Sells the current item to the high bidder (or declares unsold) via a button click.
    """
    global game_state
    if game_state["current_item"]:
        player_name = game_state["high_bidder"]
        amount = game_state["current_bid"]

        game_action = {"type": "sell_item", "item": game_state["current_item"], "player": player_name, "amount": amount}
        
        if player_name and amount > 0:
            narrative = f"Auctioneer: Going once, going twice... Sold! The '{game_state['current_item']}' goes to {player_name} for {amount} credits!"
        else:
            narrative = f"Auctioneer: With no bids for '{game_state['current_item']}', it is declared unsold!"

        game_state["chat_log"].append({"sender": "Auctioneer", "message": narrative})
        action_result = apply_game_action(game_action)
        game_state["chat_log"].append({"sender": "System", "message": f"Action processed: {action_result}"})

        return jsonify({"success": True, "game_state": game_state})
    else:
        narrative = "Auctioneer: No item currently under auction to sell."
        game_state["chat_log"].append({"sender": "Auctioneer", "message": narrative})
        return jsonify({"success": False, "message": narrative, "game_state": game_state}), 400

@app.route('/shuffle_items_action', methods=['POST'])
def shuffle_items_action():
    """
    Shuffles the remaining items in the item_list.
    """
    global game_state
    if game_state["item_list"]:
        random.shuffle(game_state["item_list"])
        game_state["chat_log"].append({"sender": "System", "message": "The remaining items have been shuffled!"})
        narrative = "Auctioneer: A little shake-up in the inventory! Items have been reordered."
        game_state["chat_log"].append({"sender": "Auctioneer", "message": narrative})
        return jsonify({"success": True, "game_state": game_state})
    else:
        narrative = "Auctioneer: No items available to shuffle yet."
        game_state["chat_log"].append({"sender": "Auctioneer", "message": narrative})
        return jsonify({"success": False, "message": narrative, "game_state": game_state}), 400


@app.route('/get_game_state', methods=['GET'])
def get_game_state_route():
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
    <title>Rule-Based Auctioneer Game</title>
    <link href="https://fonts.googleapis.com/css2?family=Montserrat:wght@400;600;700&family=Roboto:wght@300;400;500&display=swap" rel="stylesheet">
    <style>
        :root {
            --primary-color: #4CAF50; /* Green */
            --secondary-color: #388E3C; /* Darker Green */
            --accent-color: #FFC107; /* Amber */
            --background-light: #F1F8E9; /* Lightest Green */
            --background-medium: #E8F5E9; /* Very Light Green */
            --text-color: #212121;
            --border-color: #C8E6C9; /* Light Green Border */
            --shadow-light: 0 2px 5px rgba(0, 0, 0, 0.1);
            --shadow-medium: 0 5px 15px rgba(0, 0, 0, 0.08);
            --success-color: #27ae60;
            --info-color: #2196F3; /* Blue Info */
            --warning-color: #FBC02D; /* Darker Amber Warning */
            --error-color: #D32F2F; /* Red Error */
            --auction-button-color: #673AB7; /* Deep Purple */
            --auction-button-hover: #512DA8; /* Darker Purple */
        }

        /* --- Global Resets & Body Setup --- */
        html, body {
            height: 100%; /* Ensure html and body take full viewport height */
            margin: 0;
            padding: 0;
        }

        body {
            font-family: 'Roboto', sans-serif;
            background-color: var(--background-light);
            color: var(--text-color);
            display: flex;
            flex-direction: column; /* Main content stacked vertically */
            align-items: center;
            min-height: 100vh; /* Ensures body takes full viewport height */
            line-height: 1.6;
            -webkit-font-smoothing: antialiased;
            -moz-osx-font-smoothing: grayscale;
        }

        h1, h2, h3 {
            font-family: 'Montserrat', sans-serif;
            color: var(--secondary-color);
            margin-top: 0;
            margin-bottom: 1rem;
            font-weight: 600;
        }

        h1 {
            font-size: 2.8em;
            color: var(--primary-color);
            text-shadow: var(--shadow-light);
            margin-bottom: 25px;
            padding-bottom: 10px;
            border-bottom: 2px solid var(--primary-color);
            flex-shrink: 0; /* Prevents H1 from shrinking */
        }

        h2 {
            font-size: 1.8em;
            border-bottom: 1px solid var(--border-color);
            padding-bottom: 8px;
            margin-bottom: 15px;
            color: var(--secondary-color);
        }

        h3 {
            font-size: 1.3em;
            color: var(--primary-color);
            margin-bottom: 10px;
        }

        /* --- Main Container (Grid) --- */
        .container {
            display: grid;
            grid-template-columns: 1fr 1fr 2fr; /* Left, Middle, Right panels */
            grid-template-rows: minmax(0, 1fr); 
            gap: 20px;
            width: 95%;
            max-width: 1400px;
            background-color: #fff;
            box-shadow: var(--shadow-medium);
            border-radius: 12px;
            overflow: hidden; 
            margin-bottom: 25px; 
            padding: 20px;
            flex: 1; 
            box-sizing: border-box; 
            min-height: 0; 
        }

        /* --- Panels within the Grid (Left, Center, Right) --- */
        .left-panel, .center-panel, .right-panel {
            padding: 20px;
            background-color: var(--background-medium);
            border-radius: 10px;
            display: flex;
            flex-direction: column; 
            border: 1px solid var(--border-color);
            height: 100%; 
            box-sizing: border-box;
            overflow-y: auto; 
            overflow-x: hidden; 
            min-height: 0; 
        }

        /* --- Individual Sections within Panels (.panel-section) --- */
        .panel-section {
            background-color: #fff;
            padding: 15px;
            border-radius: 8px;
            margin-bottom: 20px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.05);
            border: 1px solid var(--border-color);
            flex-shrink: 0; 
            display: flex; 
            flex-direction: column; 
            min-height: 0; 
            overflow: hidden; 
        }
        .panel-section:last-child {
            margin-bottom: 0;
        }

        /* Specific section styles (these are flex-shrink: 0 children of .panel-section) */
        .game-status-section {
            margin-bottom: 20px;
        }
        .current-auction-section {
            margin-bottom: 20px;
        }
        .status-message {
            background-color: var(--primary-color);
            color: white;
            padding: 12px;
            border-radius: 6px;
            margin-bottom: 15px;
            font-weight: 500;
            text-align: center;
            box-shadow: var(--shadow-light);
            font-family: 'Montserrat', sans-serif;
        }
        .status-message.info { background-color: var(--info-color); }
        .status-message.warning { background-color: var(--warning-color); }
        .status-message.error { background-color: var(--error-color); }
        .status-message.success { background-color: var(--success-color); }

        .current-auction-section p {
            margin: 8px 0;
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding-right: 5px;
        }
        .current-auction-section strong {
            color: var(--secondary-color);
        }
        .current-auction-section span {
            font-weight: 500;
            color: var(--primary-color);
        }

        /* --- Left Panel Specifics (Item Upload and Reset Button) --- */
        .item-upload-section, .auction-controls-section, .game-management-section {
            padding-top: 20px; /* Separator from content above */
            text-align: center;
            flex-shrink: 0; 
            margin-bottom: 20px; 
        }
        .item-upload-section input[type="file"] {
            display: block;
            margin: 15px auto;
            padding: 8px;
            border: 1px solid var(--border-color);
            border-radius: 6px;
            width: calc(100% - 20px);
            max-width: 300px;
            background-color: #fcfcfc;
        }
        .item-upload-section p {
            font-size: 0.9em;
            color: #777;
            margin-top: 5px;
        }
        .auction-controls-section button,
        .game-management-section button {
            width: calc(100% - 10px); /* Adjust width to fit container with padding */
            margin: 5px 0; /* Vertical spacing between buttons */
            max-width: 300px;
        }

        /* This pushes the game management section to the bottom */
        .left-panel > .panel-section:last-child { 
            margin-top: auto; 
        }

        /* --- Scrollable List Wrappers (Content within .panel-section) --- */
        .scrollable-list-wrapper {
            flex: 1; 
            overflow-y: auto;
            border: 1px solid var(--border-color);
            border-radius: 8px;
            background-color: #fdfdfd;
            padding: 10px;
            box-shadow: inset 0 1px 3px rgba(0,0,0,0.03);
            min-height: 0; 
            margin-bottom: 10px; 
            min-width: 0; 
            word-wrap: break-word; 
        }
        /* Specific scrollable lists will now just inherit flex:1 and min-height:0 */
        #participants-list-wrapper, 
        #player-inventories-list-wrapper, 
        #items-remaining-list-wrapper, 
        #auction-history-list-wrapper {
            flex-grow: 1; 
        }

        .scrollable-list-wrapper ul {
            list-style-type: none;
            padding: 0;
            margin: 0;
        }
        .scrollable-list-wrapper li {
            padding: 10px 5px;
            border-bottom: 1px dashed #eee;
            display: flex;
            justify-content: space-between;
            align-items: center;
            font-size: 0.95em;
        }
        .scrollable-list-wrapper li:last-child {
            border-bottom: none;
        }
        .scrollable-list-wrapper li:nth-child(even) {
            background-color: #f4f4f4;
        }
        .scrollable-list-wrapper li.player-inventory-header {
            font-weight: bold;
            background-color: var(--background-medium);
            padding: 8px 5px;
            border-bottom: 2px solid var(--primary-color);
            margin-top: 10px;
            font-family: 'Montserrat', sans-serif;
            color: var(--secondary-color);
            position: sticky; 
            top: 0;
            z-index: 10;
        }
        .scrollable-list-wrapper li.player-inventory-item {
            font-style: italic;
            padding-left: 20px;
            color: #555;
            justify-content: flex-start;
        }
        .player-inventory-item .item-price {
            font-weight: 500;
            color: var(--secondary-color);
            margin-left: auto; /* Pushes price to the right */
        }


        .player-budget {
            font-weight: 600;
            color: var(--primary-color);
        }
        .player-item-count {
            font-size: 0.8em;
            color: #777;
            margin-left: 10px;
        }

        /* --- Right Panel (Chat Interface) --- */
        .chat-log {
            flex: 1; 
            overflow-y: auto; 
            overflow-x: hidden; 
            border: 1px solid var(--border-color);
            border-radius: 8px;
            padding: 15px;
            background-color: #fff;
            margin-bottom: 20px; 
            display: flex;
            flex-direction: column; 
            box-shadow: inset 0 1px 3px rgba(0,0,0,0.03);
            min-height: 350px; 
            scroll-behavior: smooth;
            word-wrap: break-word; 
        }
        .chat-message {
            margin-bottom: 12px;
            padding: 8px 12px;
            border-radius: 8px;
            max-width: 90%;
            word-wrap: break-word; 
            font-size: 0.95em;
        }
        .chat-message:last-child {
            margin-bottom: 0;
        }
        .chat-message strong {
            font-family: 'Montserrat', sans-serif;
            font-weight: 600;
            margin-right: 5px;
        }
        .chat-message.You {
            background-color: #e8f5e9; /* Light green */
            align-self: flex-end;
            text-align: right;
            border-bottom-right-radius: 0;
        }
        .chat-message.You strong {
            color: var(--accent-color);
        }
        .chat-message.Auctioneer {
            background-color: #e3f2fd; /* Light blue */
            align-self: flex-start;
            text-align: left;
            border-bottom-left-radius: 0;
        }
        .chat-message.Auctioneer strong {
            color: var(--primary-color);
        }
        .chat-message.System {
            background-color: #fffde7; /* Light yellow */
            align-self: center;
            text-align: center;
            color: #555;
            font-style: italic;
            border: 1px dashed #ffe082;
            width: 100%;
            max-width: none; 
        }
        .chat-message.System strong {
            color: var(--info-color);
        }

        .chat-input {
            display: flex;
            padding-top: 15px;
            flex-shrink: 0; 
            margin-top: auto; 
            border-top: 1px solid var(--border-color);
            background-color: var(--background-medium); 
            padding-bottom: 5px; 
        }
        .chat-input input[type="text"] {
            flex: 1;
            padding: 14px 18px;
            border: 1px solid var(--border-color);
            border-radius: 8px;
            font-size: 1.05em;
            margin-right: 12px;
            transition: border-color 0.2s ease-in-out, box-shadow 0.2s ease-in-out;
            background-color: #fdfdfd;
        }
        .chat-input input[type="text"]:focus {
            outline: none;
            border-color: var(--primary-color);
            box-shadow: 0 0 0 3px rgba(76, 175, 80, 0.2); 
        }
        
        button {
            padding: 14px 25px;
            background-color: var(--primary-color);
            color: white;
            border: none;
            border-radius: 8px;
            cursor: pointer;
            font-size: 1.05em;
            font-weight: 600;
            transition: background-color 0.2s ease, transform 0.1s ease;
            box-shadow: var(--shadow-light);
            flex-shrink: 0; 
        }
        button:hover {
            background-color: var(--secondary-color); 
            transform: translateY(-1px);
        }
        button:active {
            transform: translateY(0);
            box-shadow: inset 0 1px 3px rgba(0, 0, 0, 0.2);
        }
        button:disabled {
            background-color: #cccccc;
            cursor: not-allowed;
            box-shadow: none;
            transform: none;
        }

        .item-upload-section button {
            background-color: var(--info-color); 
        }
        .item-upload-section button:hover {
            background-color: #1976D2; 
        }
        .reset-game-button {
            background-color: var(--error-color); 
            margin-top: 20px; 
        }
        .reset-game-button:hover {
            background-color: #C62828; 
        }
        /* Auction specific buttons */
        .auction-control-button {
            background-color: var(--auction-button-color);
        }
        .auction-control-button:hover {
            background-color: var(--auction-button-hover);
        }

        .game-over-message {
            text-align: center;
            font-size: 1.8em;
            font-weight: 700;
            color: var(--error-color);
            margin-top: 30px;
            padding: 20px;
            background-color: #ffebee; 
            border-radius: 10px;
            border: 2px solid var(--error-color);
            box-shadow: var(--shadow-medium);
        }
        
        footer {
            margin-top: 30px;
            padding: 20px;
            color: #888;
            font-size: 0.9em;
            text-align: center;
            border-top: 1px solid var(--border-color);
            width: 90%;
            max-width: 1400px;
            flex-shrink: 0; 
            margin-top: auto; 
        }

        /* --- Responsive adjustments --- */
        @media (max-width: 1200px) {
            .container {
                grid-template-columns: 1.5fr 2fr; 
                grid-template-areas:
                    "info chat"
                    "lists chat";
                max-width: 1000px;
                grid-template-rows: minmax(0, 1fr); 
            }
            .left-panel {
                grid-area: info;
            }
            .center-panel {
                grid-area: lists;
            }
            .right-panel {
                grid-area: chat;
            }
            .chat-log {
                min-height: 300px; 
            }
        }

        @media (max-width: 900px) {
            .container {
                grid-template-columns: 1fr; 
                grid-template-areas:
                    "info"
                    "lists"
                    "chat";
                padding: 15px;
                gap: 15px;
                min-height: unset; 
                grid-template-rows: auto; 
            }
            h1 {
                font-size: 2em;
            }
            .left-panel, .center-panel, .right-panel {
                padding: 15px;
                height: auto; 
                overflow-y: visible; 
            }
            .chat-log {
                min-height: 250px; 
            }
            .chat-input {
                padding-top: 10px;
                padding-bottom: 0;
            }
            .chat-input input[type="text"], button {
                padding: 12px 15px;
                font-size: 0.95em;
            }
        }

        @media (max-width: 600px) {
            .container {
                grid-template-rows: auto; 
            }
            .left-panel, .center-panel, .right-panel {
                height: auto; 
                overflow-y: visible; 
            }
            .chat-log {
                min-height: 200px; 
            }
        }
    </style>
</head>
<body>
    <h1>Rule-Based Auctioneer Game</h1>
    <div class="container">
        <!-- Left Panel: Game Status & Current Auction & Item Upload -->
        <div class="left-panel">
            <div class="panel-section game-status-section">
                <h2>Game Status</h2>
                <div id="status-message" class="status-message info">Loading game...</div>
            </div>

            <div class="panel-section current-auction-section">
                <h3>Current Auction</h3>
                <p><strong>Item:</strong> <span id="auction-item">None</span></p>
                <p><strong>Current Bid:</strong> <span id="current-bid">0</span> credits</p>
                <p><strong>High Bidder:</strong> <span id="high-bidder">None</span></p>
            </div>
            
            <div class="panel-section auction-controls-section">
                <h3>Auction Actions</h3>
                <button id="start-auction-btn" class="auction-control-button" onclick="startNextAuction()" disabled>Start Next Auction</button>
                <button id="sell-item-btn" class="auction-control-button" onclick="sellCurrentItem()" disabled>Sell Current Item</button>
                <button id="shuffle-items-btn" class="auction-control-button" onclick="shuffleItems()" disabled>Shuffle Remaining Items</button>
            </div>

            <div class="panel-section item-upload-section">
                <h3>Upload Items from File</h3>
                <input type="file" id="item-file-input" accept=".csv, .txt">
                <button onclick="uploadItems()">Upload Items</button>
                <p><i>(File should contain one item name per line)</i></p>
            </div>

            <div class="panel-section game-management-section">
                <button onclick="resetGame()" class="reset-game-button">Start New Game / Reset</button>
            </div>
        </div>

        <!-- Middle Panel: Participants, Items Remaining, Player Inventories, Auction History -->
        <div class="center-panel">
            <div class="panel-section">
                <h2>Participants</h2>
                <div id="participants-list-wrapper" class="scrollable-list-wrapper">
                    <ul id="participants-list">
                        <li>No participants yet.</li>
                    </ul>
                </div>
            </div>

            <div class="panel-section">
                <h2>Items Remaining</h2>
                <div id="items-remaining-list-wrapper" class="scrollable-list-wrapper">
                    <ul id="items-remaining-list">
                        <li>No items yet.</li>
                    </ul>
                </div>
            </div>

            <div class="panel-section">
                <h2>Player Inventories</h2>
                <div id="player-inventories-list-wrapper" class="scrollable-list-wrapper">
                    <ul id="player-inventories-list">
                        <li>No items purchased yet.</li>
                    </ul>
                </div>
            </div>
            
            <div class="panel-section">
                <h2>Auction History</h2>
                <div id="auction-history-list-wrapper" class="scrollable-list-wrapper">
                    <ul id="auction-history-list">
                        <li>No items sold yet.</li>
                    </ul>
                </div>
            </div>
        </div>

        <!-- Right Panel: Chat Interface -->
        <div class="right-panel">
            <h2>Auction Chat</h2>
            <div id="chat-log" class="chat-log">
                <!-- Chat messages will be dynamically inserted here -->
            </div>
            <div class="chat-input">
                <input type="text" id="user-message" placeholder="Type your message or bid here...">
                <button onclick="sendMessage()">Send</button>
            </div>
        </div>
    </div>
    <footer>
        Powered by Rule-Based Logic (Python/Flask). Designed for zero human effort deployment.
    </footer>

    <script>
        document.addEventListener('DOMContentLoaded', () => {
            fetchGameState(); // Fetch initial state on load
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
                // Optionally display a user-friendly error message
                addChatMessage('System Error', 'Could not connect to the server. Please refresh.', 'System');
            }
        }

        async function sendMessage() {
            const userMessageInput = document.getElementById('user-message');
            const message = userMessageInput.value.trim();
            if (!message) return;

            userMessageInput.value = ''; // Clear input field

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
            } else {
                console.error('Error processing chat:', data.message);
                addChatMessage('System Error', data.message, 'System');
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
                    addChatMessage('System', data.message, 'System');
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

        async function resetGame() {
            if (!confirm('Are you sure you want to reset the game? All current progress will be lost.')) {
                return;
            }
            try {
                const response = await fetch('/reset_game', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify({}), // Empty body for a reset action
                });
                const data = await response.json();
                if (data.success) {
                    updateUI(data.game_state);
                    // Clear existing chat log and then re-add initial messages
                    const chatLog = document.getElementById('chat-log');
                    chatLog.innerHTML = '';
                    for (const chatEntry of data.game_state.chat_log) {
                        addChatMessage(chatEntry.sender, chatEntry.message, chatEntry.sender);
                    }
                    chatLog.scrollTop = chatLog.scrollHeight;
                } else {
                    alert('Failed to reset game: ' + data.message);
                }
            } catch (error) {
                console.error('Error resetting game:', error);
                alert('Network error during game reset.');
            }
        }

        async function startNextAuction() {
            try {
                const response = await fetch('/start_next_auction_action', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({})
                });
                const data = await response.json();
                if (data.success) {
                    updateUI(data.game_state);
                } else {
                    alert('Error starting auction: ' + data.message);
                }
            } catch (error) {
                console.error('Error starting auction:', error);
                alert('Network error during auction start.');
            }
        }

        async function sellCurrentItem() {
            try {
                const response = await fetch('/sell_current_item_action', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({})
                });
                const data = await response.json();
                if (data.success) {
                    updateUI(data.game_state);
                } else {
                    alert('Error selling item: ' + data.message);
                }
            } catch (error) {
                console.error('Error selling item:', error);
                alert('Network error during item sale.');
            }
        }

        async function shuffleItems() {
            try {
                const response = await fetch('/shuffle_items_action', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({})
                });
                const data = await response.json();
                if (data.success) {
                    updateUI(data.game_state);
                } else {
                    alert('Error shuffling items: ' + data.message);
                }
            } catch (error) {
                console.error('Error shuffling items:', error);
                alert('Network error during item shuffle.');
            }
        }

        function addChatMessage(sender, message, type) {
            const chatLog = document.getElementById('chat-log');
            const div = document.createElement('div');
            div.className = `chat-message ${type}`;
            div.innerHTML = `<strong>${sender}:</strong> ${message}`;
            chatLog.appendChild(div);
            chatLog.scrollTop = chatLog.scrollHeight; // Scroll to the latest message
        }

        function updateUI(gameState) {
            // Update Status Message
            const statusMessageDiv = document.getElementById('status-message');
            let statusText = "Game Status: ";
            let statusClass = "info"; 

            if (gameState.status === "waiting_for_init") {
                statusText += "Waiting for game initialization.";
                statusClass = "warning";
            } else if (gameState.status === "waiting_for_items") {
                statusText += "Game initialized. Waiting for items.";
                statusClass = "warning";
            } else if (gameState.status === "waiting_for_auction_start") {
                statusText += "Items added. Waiting to start auction.";
                statusClass = "warning";
            } else if (gameState.status === "bidding") {
                statusText += `Auction for '${gameState.current_item}' is active.`;
                statusClass = "success";
            } else if (gameState.status === "game_over") {
                statusText += "Game Over - All items processed!";
                statusClass = "error";
            }
            statusMessageDiv.textContent = statusText;
            statusMessageDiv.className = `status-message ${statusClass}`;

            // Update Current Auction Info
            document.getElementById('auction-item').textContent = gameState.current_item || 'None';
            document.getElementById('current-bid').textContent = gameState.current_bid || 0;
            document.getElementById('high-bidder').textContent = gameState.high_bidder || 'None';

            // Enable/Disable Auction Control Buttons
            const startAuctionBtn = document.getElementById('start-auction-btn');
            const sellItemBtn = document.getElementById('sell-item-btn');
            const shuffleItemsBtn = document.getElementById('shuffle-items-btn');

            startAuctionBtn.disabled = !gameState.item_list.length || gameState.status === "bidding" || gameState.status === "game_over";
            sellItemBtn.disabled = !gameState.current_item || gameState.status !== "bidding";
            shuffleItemsBtn.disabled = !gameState.item_list.length;


            // Update Participants List
            const participantsList = document.getElementById('participants-list');
            participantsList.innerHTML = '';
            if (Object.keys(gameState.participants).length === 0) {
                participantsList.innerHTML = '<li>No participants yet.</li>';
            } else {
                for (const player in gameState.participants) {
                    const li = document.createElement('li');
                    const ownedItemsCount = gameState.player_items[player] ? gameState.player_items[player].length : 0;
                    li.innerHTML = `<span>${player}</span> 
                                    <span class="player-budget">${gameState.participants[player]} credits</span>
                                    <span class="player-item-count">(${ownedItemsCount} items)</span>`;
                    participantsList.appendChild(li);
                }
            }

            // Update Items Remaining List
            const itemsRemainingList = document.getElementById('items-remaining-list');
            itemsRemainingList.innerHTML = '';
            const allItems = [...gameState.item_list]; // Copy for items remaining
            if (gameState.current_item && !allItems.includes(gameState.current_item)) { // Only add if not already explicitly in list (i.e. if it's currently being auctioned)
                allItems.unshift(gameState.current_item + " (current)"); 
            }

            if (allItems.length === 0) {
                itemsRemainingList.innerHTML = '<li>No items remaining.</li>';
            } else {
                allItems.forEach(item => {
                    const li = document.createElement('li');
                    li.textContent = item;
                    itemsRemainingList.appendChild(li);
                });
            }

            // Update Player Inventories List - NOW SHOWS PRICE
            const playerInventoriesList = document.getElementById('player-inventories-list');
            playerInventoriesList.innerHTML = '';
            let hasItemsBought = false;
            for (const player in gameState.player_items) {
                if (gameState.player_items[player].length > 0) {
                    hasItemsBought = true;
                    const headerLi = document.createElement('li');
                    headerLi.className = 'player-inventory-header';
                    headerLi.textContent = `${player}'s Inventory`;
                    playerInventoriesList.appendChild(headerLi);
                    gameState.player_items[player].forEach(item => {
                        const itemLi = document.createElement('li');
                        itemLi.className = 'player-inventory-item';
                        itemLi.innerHTML = `${item.name} <span class="item-price">(${item.price} credits)</span>`; // Display name and price
                        playerInventoriesList.appendChild(itemLi);
                    });
                }
            }
            if (!hasItemsBought) {
                playerInventoriesList.innerHTML = '<li>No items purchased yet.</li>';
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
            // Clear current chat log completely and rebuild to avoid complex diffing logic
            chatLog.innerHTML = '';
            for (const chatEntry of gameState.chat_log) {
                addChatMessage(chatEntry.sender, chatEntry.message, chatEntry.sender);
            }
            chatLog.scrollTop = chatLog.scrollHeight; // Ensure it scrolls to bottom after full rebuild
        }
    </script>
</body>
</html>
"""

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)