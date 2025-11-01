import os
import json
import random
import re
import csv
import io
import copy
import sys # For getting object size, helpful for debugging memory (not used in final, but useful for diagnostics)

from flask import Flask, request, jsonify, render_template_string

# --- Configuration ---
app = Flask(__name__)

# --- Game State (Server now primarily provides structure, not persistent state) ---
def get_initial_game_state():
    return {
        "participants": {},
        "player_items": {},
        "item_list": [],
        "auction_history": [],
        "current_item": None,
        "current_bid": 0,
        "high_bidder": None,
        "status": "waiting_for_init",
        "chat_log": [{"sender": "Auctioneer", "message": "Welcome! To begin, type: `start game players John, Jane budget 100` (Or add your own player names and budget!)."}],
        "last_processed_action_hash": None, # Stored per-client, but needed for server-side logic
        "player_inventory_sort": {"key": "name", "order": "asc"},
        "initial_budget": 0
    }

# The server no longer manages a global persistent game_state.
# Instead, it operates on the state provided by the client for each request.
# The `game_state_history` will also be client-side now for undo/redo.
# We'll keep a reference to a default initial state for 'reset' and initial load.
DEFAULT_INITIAL_GAME_STATE = get_initial_game_state()

# --- Rule-Based Command Processing ---

# This function remains largely the same, but it now explicitly takes `current_game_state_for_logic`
# as an argument, rather than relying on a global server-side `game_state`.
def process_user_command(user_input, current_game_state_for_logic):
    """
    Parses user input using rule-based logic to determine game actions and narratives.
    Returns (narrative, game_action).
    """
    narrative = ""
    game_action = {"type": "no_action"}
    
    user_input_lower = user_input.lower().strip()

    # 1. Initialize Game (Simplified)
    match = re.match(r"start game players ([\w,\s]+) budget (\d+)\.?$", user_input_lower)
    if match and current_game_state_for_logic["status"] == "waiting_for_init":
        player_names = [p.strip() for p in match.group(1).split(',') if p.strip()]
        budget = int(match.group(2))
        if player_names and budget > 0:
            narrative = f"Welcome, {', '.join(p.title() for p in player_names)}! Each of you starts with {budget} credits. Let the game begin!"
            game_action = {"type": "init_game", "players": player_names, "budget": budget}
            return narrative, game_action
        else:
            narrative = "Invalid players or budget specified for starting the game. Please use: `start game players John, Jane budget 100`."
            return narrative, {"type": "no_action"}
    elif match and current_game_state_for_logic["status"] != "waiting_for_init":
        narrative = "A game is already in progress. Please reset the game to start a new one."
        return narrative, {"type": "no_action"}

    # 2. Add Items (Simplified)
    match = re.match(r"add (.*)", user_input_lower)
    if match and current_game_state_for_logic["status"] != "waiting_for_init":
        items_str = match.group(1)
        items = [item.strip() for item in items_str.split(',') if item.strip()]
        if items:
            narrative = f"Excellent! We have {', '.join(item.title() for item in items)} ready for auction."
            game_action = {"type": "add_items", "items": items}
            return narrative, game_action
        else:
            narrative = "No items specified to add. Please use: `add Car, House, Boat`"
            return narrative, {"type": "no_action"}

    # 3. Shuffle Items (Simplified)
    if user_input_lower == "shuffle":
        if current_game_state_for_logic["item_list"]:
            narrative = "Auctioneer: A little shake-up in the inventory! Items have been reordered."
            game_action = {"type": "shuffle_items"}
            return narrative, game_action
        else:
            narrative = "Auctioneer: No items available to shuffle yet. Please `add Car, House` first."
            return narrative, {"type": "no_action"}

    # 4. Explicit "No Sale" command
    if user_input_lower == "no sale":
        if current_game_state_for_logic["current_item"]:
            narrative = f"As there are no valid bids on the '{current_game_state_for_logic['current_item']}', it remains unsold for now. Perhaps it will return later, or we move on."
            game_action = {"type": "sell_item", "item": current_game_state_for_logic["current_item"], "player": None, "amount": 0}
            return narrative, game_action
        else:
            narrative = "There is no item currently under auction to declare 'no sale'. Please `auction Car` first."
            return narrative, {"type": "no_action"}

    # 5. Sell Item (Explicit - Simplified: `sell John 30`) - MUST COME BEFORE IMPLICIT SELL
    match = re.match(r"sell ([\w\s]+) (\d+)\.?$", user_input_lower)
    if match and current_game_state_for_logic["current_item"]:
        player_name = match.group(1).strip().title()
        amount = int(match.group(2))
        
        if player_name not in current_game_state_for_logic["participants"]:
            narrative = f"Player '{player_name}' not recognized. Cannot sell item. Recognized players: {', '.join(current_game_state_for_logic['participants'].keys())}."
            return narrative, {"type": "no_action"}
        if amount > current_game_state_for_logic["participants"][player_name]:
            narrative = f"'{player_name}' cannot afford {amount} credits for '{current_game_state_for_logic['current_item']}'. Sale cancelled."
            return narrative, {"type": "no_action"}
        
        narrative = f"Sold! The '{current_game_state_for_logic['current_item']}' goes to {player_name} for {amount} credits!"
        game_action = {"type": "sell_item", "item": current_game_state_for_logic["current_item"], "player": player_name, "amount": amount}
        return narrative, game_action
    elif match and not current_game_state_for_logic["current_item"]:
        narrative = "There is no item currently under auction to sell. Please `auction Car` first."
        return narrative, {"type": "no_action"}

    # 6. Sell Item (Implicit - Simplified: `sell it`) - MUST COME AFTER EXPLICIT SELL
    if user_input_lower == "sell it":
        if current_game_state_for_logic["current_item"]:
            if current_game_state_for_logic["high_bidder"] and current_game_state_for_logic["current_bid"] > 0:
                player_name = current_game_state_for_logic["high_bidder"]
                amount = current_game_state_for_logic["current_bid"]
                narrative = f"Sold! The '{current_game_state_for_logic['current_item']}' goes to {player_name} for {amount} credits!"
                game_action = {"type": "sell_item", "item": current_game_state_for_logic["current_item"], "player": player_name, "amount": amount}
            else:
                narrative = f"As there are no valid bids on the '{current_game_state_for_logic['current_item']}', it remains unsold for now. Perhaps it will return later, or we move on."
                game_action = {"type": "sell_item", "item": current_game_state_for_logic["current_item"], "player": None, "amount": 0}
            return narrative, game_action
        else:
            narrative = "There is no item currently under auction to sell. Please `auction Car` first."
            return narrative, {"type": "no_action"}

    # 7. Start Item Auction (Simplified: `auction Car` or `auction first`)
    match = re.match(r"auction (?:first|next|([a-zA-Z0-9\s]+))\b\.?", user_input_lower)
    if match and current_game_state_for_logic["status"] != "waiting_for_init" and current_game_state_for_logic["item_list"]:
        item_name_group = match.group(1)
        item_to_start_name = None

        if item_name_group: # User specified an item name
            item_to_start_name = item_name_group.strip().title()
        elif current_game_state_for_logic["item_list"]: # "first" or "next"
            item_to_start_name = current_game_state_for_logic["item_list"][0].title() # Use the first available item

        if not item_to_start_name:
            narrative = "No item found to start an auction for. Please add items or specify a valid item name."
            return narrative, {"type": "no_action"}

        if item_to_start_name == current_game_state_for_logic["current_item"] and current_game_state_for_logic["status"] == "bidding":
            narrative = f"Auction for '{item_to_start_name}' is already underway! What's your bid?"
            return narrative, {"type": "no_action"}
        
        # Check if the requested item is actually in the list (if specified by name)
        if item_to_start_name in current_game_state_for_logic["item_list"] or \
           (item_to_start_name.endswith(" (current)") and item_to_start_name.replace(" (current)","") in current_game_state_for_logic["item_list"]):
            if item_to_start_name.endswith(" (current)"):
                item_to_start_name = item_to_start_name.replace(" (current)","")

            narrative = f"Our next item up for bid is the magnificent '{item_to_start_name}'! Who will start us off? Bids begin at 1 credit."
            game_action = {"type": "start_item_auction", "item": item_to_start_name}
            return narrative, game_action
        else:
            narrative = f"Item '{item_to_start_name}' not found in the list of available items. Available: {', '.join(current_game_state_for_logic['item_list'])}. Please use: `auction Car` or `auction first`."
            return narrative, {"type": "no_action"}
    elif match and not current_game_state_for_logic["item_list"]:
        narrative = "There are no items available to auction yet. Please add some first using: `add Car, House`."
        return narrative, {"type": "no_action"}
    elif match and current_game_state_for_logic["status"] == "bidding":
        narrative = f"An auction for '{current_game_state_for_logic['current_item']}' is already in progress. Please bid or sell it first using: `John bid 10` or `sell it!`."
        return narrative, {"type": "no_action"}

    # 8. Place Bid (Simplified: `John bid 10`)
    match = re.match(r"([\w\s]+) bid (\d+)\.?$", user_input_lower)
    if match and current_game_state_for_logic["status"] == "bidding":
        player_name = match.group(1).strip().title()
        amount = int(match.group(2))

        if player_name not in current_game_state_for_logic["participants"]:
            narrative = f"Player '{player_name}' is not a recognized participant. Please ensure the player name is correct. Recognized players: {', '.join(current_game_state_for_logic['participants'].keys())}."
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
        narrative = "No item is currently under auction. You need to start an auction first. Use: `auction Car`."
        return narrative, {"type": "no_action"}

    # 9. Player Passes (Simplified: `John pass`)
    match = re.match(r"([\w\s]+) pass\.?", user_input_lower)
    if match and current_game_state_for_logic["current_item"]:
        player_name = match.group(1).strip().title()
        if player_name not in current_game_state_for_logic["participants"]:
            narrative = f"Player '{player_name}' not recognized. Recognized players: {', '.join(current_game_state_for_logic['participants'].keys())}."
            return narrative, {"type": "no_action"}
        narrative = f"{player_name} passes on '{current_game_state_for_logic['current_item']}'. Any other bids?"
        game_action = {"type": "pass", "player": player_name}
        return narrative, game_action
    elif match and not current_game_state_for_logic["current_item"]:
        narrative = "No item is currently under auction to pass on."
        return narrative, {"type": "no_action"}
        
    return f"Auctioneer: I didn't understand your command: '{user_input}'. Please try again with a clear instruction, or refer to the 'Command Assistant' for examples. Common commands include: `John bid 10`, `sell it!`, `auction Car`, `no sale`.", {"type": "no_action"}


# --- Game Logic Functions ---

# `apply_game_action` now takes `current_game_state` as an argument
def apply_game_action(action, current_game_state):
    """
    Applies a parsed GAME_ACTION to a given game_state.
    Returns a tuple: (modified_game_state, result_message_string, bool_state_actually_changed).
    """
    # Create a deep copy to work on, ensuring no side effects on the original until explicitly returned
    modified_game_state = copy.deepcopy(current_game_state)
    
    action_type = action.get("type")
    state_actually_changed = False
    result_message = ""

    # Deduplicate actions: compute hash before any state changes
    action_hash = hash(json.dumps(action, sort_keys=True))
    
    if action_type not in ["pass", "no_action"] and action_hash == modified_game_state.get("last_processed_action_hash"):
        print(f"Skipping duplicate action: {action_type}")
        return modified_game_state, "Duplicate action skipped.", False

    # Set hash for the action being processed
    if action_type != "no_action":
        modified_game_state["last_processed_action_hash"] = action_hash
        state_actually_changed = True # Assume state will change for non-no_action types

    # Now apply the action to modified_game_state
    if action_type == "init_game":
        players = action.get("players")
        budget = action.get("budget")
        if not players or budget is None:
            return current_game_state, "Error: Missing players or budget for init_game.", False # Return original state
        
        modified_game_state["participants"] = {p.title(): budget for p in players}
        modified_game_state["player_items"] = {p.title(): [] for p in players}
        modified_game_state["initial_budget"] = budget
        modified_game_state["status"] = "waiting_for_items"
        modified_game_state["chat_log"] = DEFAULT_INITIAL_GAME_STATE["chat_log"].copy() # Reset chat on new game
        modified_game_state["chat_log"].append({"sender": "System", "message": f"Game initialized with players: {', '.join(modified_game_state['participants'].keys())}. Each has {budget} credits."})
        result_message = f"Game initialized for {len(players)} players."

    elif action_type == "add_items":
        items = action.get("items")
        if not items:
            return current_game_state, "Error: No items provided for add_items.", False
        
        items_to_add = [item.strip().title() for item in items if item.strip()]
        modified_game_state["item_list"].extend(items_to_add)
        if modified_game_state["status"] == "waiting_for_items":
            modified_game_state["status"] = "waiting_for_auction_start"
            modified_game_state["chat_log"].append({"sender": "Auctioneer", "message": f"Excellent, items have been added! You can now use the 'Auction Next Item' button or type 'auction first'."})
        modified_game_state["chat_log"].append({"sender": "System", "message": f"Items added: {', '.join(items_to_add)}."})
        result_message = f"Added {len(items_to_add)} items."

    elif action_type == "start_item_auction":
        item_name = action.get("item")
        if not item_name:
            return current_game_state, "Error: Missing item name for start_item_auction.", False
        
        if item_name == modified_game_state["current_item"] and modified_game_state["status"] == "bidding":
            return current_game_state, f"Auction for '{item_name}' is already underway.", False
        
        if item_name not in modified_game_state["item_list"]:
            return current_game_state, f"Error: Item '{item_name}' not found in available items or already auctioned.", False

        modified_game_state["current_item"] = item_name
        modified_game_state["current_bid"] = 0 
        modified_game_state["high_bidder"] = None
        modified_game_state["status"] = "bidding"
        modified_game_state["chat_log"].append({"sender": "System", "message": f"Auction for '{item_name}' has started! Current bid: {modified_game_state['current_bid']}"})
        result_message = f"Auction started for '{item_name}'."

    elif action_type == "bid":
        player = action.get("player").title()
        amount = action.get("amount")

        if not player or amount is None:
            return current_game_state, "Error: Missing player or amount for bid.", False
        if player not in modified_game_state["participants"]:
            return current_game_state, f"Error: Player '{player}' not recognized.", False
        if modified_game_state["current_item"] is None:
             return current_game_state, "Error: No item is currently being auctioned to bid on.", False
        if amount <= modified_game_state["current_bid"]:
            return current_game_state, f"Error: Bid of {amount} is not higher than current bid of {modified_game_state['current_bid']}. Minimum bid is {modified_game_state['current_bid'] + 1}.", False
        if amount > modified_game_state["participants"][player]:
            return current_game_state, f"Error: Player '{player}' does not have enough budget ({modified_game_state['participants'][player]}) for a bid of {amount}.", False
        
        modified_game_state["current_bid"] = amount
        modified_game_state["high_bidder"] = player
        modified_game_state["chat_log"].append({"sender": "System", "message": f"{player} bids {amount} credits for '{modified_game_state['current_item']}'."})
        result_message = f"Bid updated: {player} at {amount}."

    elif action_type == "sell_item":
        item_to_sell = action.get("item")
        actual_player = action.get("player")
        actual_amount = action.get("amount")

        if item_to_sell is None:
             return current_game_state, "Error: No item is currently under auction to be sold or declared unsold.", False

        if item_to_sell in modified_game_state["item_list"]:
            modified_game_state["item_list"].remove(item_to_sell)
        
        sale_successful = False
        if actual_player and actual_player in modified_game_state["participants"] and actual_amount > 0:
            if modified_game_state["participants"][actual_player] < actual_amount:
                modified_game_state["chat_log"].append({"sender": "System", "message": f"Error: Player '{actual_player}' cannot afford {actual_amount} credits for '{item_to_sell}'. Item declared UNSOLD due to affordability."})
                modified_game_state["auction_history"].append(f"'{item_to_sell}' was declared UNSOLD (affordability issue).")
            else:
                modified_game_state["participants"][actual_player] -= actual_amount
                # Ensure player_items key exists for the player
                if actual_player not in modified_game_state["player_items"]:
                    modified_game_state["player_items"][actual_player] = []
                modified_game_state["player_items"][actual_player].append({"name": item_to_sell, "price": actual_amount})
                modified_game_state["auction_history"].append(f"'{item_to_sell}' sold to {actual_player} for {actual_amount} credits.")
                modified_game_state["chat_log"].append({"sender": "System", "message": f"'{item_to_sell}' sold to {actual_player} for {actual_amount} credits. {actual_player}'s new budget: {modified_game_state['participants'][actual_player]}."})
                sale_successful = True
        
        if not sale_successful:
            modified_game_state["auction_history"].append(f"'{item_to_sell}' was declared UNSOLD (no valid bids).")
            modified_game_state["chat_log"].append({"sender": "System", "message": f"'{item_to_sell}' declared UNSOLD. No valid bids were received."})
        
        modified_game_state["current_item"] = None
        modified_game_state["current_bid"] = 0
        modified_game_state["high_bidder"] = None
        
        if not modified_game_state["item_list"]:
            modified_game_state["status"] = "game_over"
            modified_game_state["chat_log"].append({"sender": "System", "message": "All items sold or declared unsold! Game Over. Reset the game to play again."})
            result_message = "Game Over: All items processed."
        else:
            next_item = modified_game_state["item_list"][0]
            modified_game_state["current_item"] = next_item
            modified_game_state["current_bid"] = 0 
            modified_game_state["high_bidder"] = None
            modified_game_state["status"] = "bidding"
            modified_game_state["chat_log"].append({"sender": "System", "message": f"Auction for '{next_item}' has started! Current bid: {modified_game_state['current_bid']}"})
            result_message = f"Sale processed. Auction for '{next_item}' has now started."

    elif action_type == "pass":
        player = action.get("player")
        modified_game_state["chat_log"].append({"sender": "System", "message": f"{player} passes on '{modified_game_state['current_item']}'."})
        result_message = f"{player} passed."
        state_actually_changed = False # A "pass" doesn't change core game state, only logs it

    elif action_type == "shuffle_items":
        if modified_game_state["item_list"]:
            random.shuffle(modified_game_state["item_list"])
            modified_game_state["chat_log"].append({"sender": "System", "message": "The remaining items have been shuffled!"})
            result_message = "Items shuffled."
        else:
            result_message = "No items to shuffle."
            state_actually_changed = False

    elif action_type == "no_action":
        result_message = "No specific game action identified from your input."
        state_actually_changed = False

    else:
        result_message = f"Unknown game action type: {action_type}"
        state_actually_changed = False

    return modified_game_state, result_message, state_actually_changed


# --- Flask Routes ---

@app.route('/')
def index():
    """Serves the main HTML page."""
    return render_template_string(HTML_CONTENT)

# Helper to get game state from request and validate (basic).
# If no state provided, it defaults to initial.
def get_state_from_request():
    try:
        # data will be the full payload, e.g., {'message': '...', 'game_state': {...}}
        data = request.json 
        if data is None: # If request.json failed to parse or was empty
            raise ValueError("Request body was not valid JSON or was empty.")
        
        client_game_state = data.get('game_state') # Extracts the nested game_state
        if not client_game_state:
            raise ValueError("No 'game_state' key provided in request JSON.")
        
        # Basic validation and key population for safety
        # This handles cases where new keys are added to get_initial_game_state
        for key in DEFAULT_INITIAL_GAME_STATE.keys():
            if key not in client_game_state:
                client_game_state[key] = DEFAULT_INITIAL_GAME_STATE[key]
        return client_game_state
    except Exception as e:
        print(f"Error parsing client game state from request.json: {e}. Returning initial default.")
        # Print the full traceback for better debugging
        import traceback
        traceback.print_exc()
        return DEFAULT_INITIAL_GAME_STATE

@app.route('/process_chat', methods=['POST'])
def process_chat_route():
    user_input = request.json.get('message')
    client_game_state = get_state_from_request()

    if not user_input:
        return jsonify({"success": False, "message": "No message provided.", "game_state": client_game_state}), 400

    # Frontend adds 'You' message instantly. Server adds 'Auctioneer' and 'System' messages.
    narrative, game_action = process_user_command(user_input, client_game_state)
    
    # Add narrative from rule-based system
    client_game_state["chat_log"].append({"sender": "Auctioneer", "message": narrative})

    if game_action and game_action.get("type") != "no_action":
        new_game_state, action_result_msg, state_actually_changed = apply_game_action(game_action, client_game_state)
        
        if action_result_msg != "Duplicate action skipped." and state_actually_changed:
            new_game_state["chat_log"].append({"sender": "System", "message": f"Action processed: {action_result_msg}"})
        
        # Ensure player_items consistency for new players
        for p in new_game_state["participants"]:
            if p not in new_game_state["player_items"]:
                new_game_state["player_items"][p] = []

        return jsonify({
            "success": True,
            "narrative": narrative,
            "game_state": new_game_state, # Return the modified state to client
        })
    else:
        # No action, just chat update or error message
        return jsonify({
            "success": True, # Still a successful chat processing
            "narrative": narrative,
            "game_state": client_game_state # Return original state if no game action
        })

@app.route('/upload_items', methods=['POST'])
def upload_items():
    file = request.files.get('file')
    # CRITICAL FIX: For FormData, game_state is in request.form, not request.json
    client_game_state_str = request.form.get('game_state')
    
    # Default to initial state if string is missing or malformed
    client_game_state = DEFAULT_INITIAL_GAME_STATE
    if client_game_state_str:
        try:
            client_game_state = json.loads(client_game_state_str)
            # Basic validation/defaulting for loaded state as in get_state_from_request
            for key in DEFAULT_INITIAL_GAME_STATE.keys():
                if key not in client_game_state:
                    client_game_state[key] = DEFAULT_INITIAL_GAME_STATE[key]
        except json.JSONDecodeError as e:
            print(f"Error decoding game_state string from form data: {e}. Using initial default state.")
            import traceback
            traceback.print_exc()
            client_game_state = DEFAULT_INITIAL_GAME_STATE
        except Exception as e:
            print(f"Unexpected error processing game_state from form data: {e}. Using initial default state.")
            import traceback
            traceback.print_exc()
            client_game_state = DEFAULT_INITIAL_GAME_STATE


    if not file or file.filename == '':
        return jsonify({"success": False, "message": "No file selected or provided.", "game_state": client_game_state}), 400

    if not (file.filename.endswith('.csv') or file.filename.endswith('.txt')):
        return jsonify({"success": False, "message": "Invalid file type. Please upload a .csv or .txt file.", "game_state": client_game_state}), 400

    try:
        items = []
        file_content = file.read().decode('utf-8').strip()

        if file.filename.endswith('.csv'):
            csv_reader = csv.reader(io.StringIO(file_content))
            for row in csv_reader:
                if row:
                    items.append(row[0].strip())
        else: # .txt file
            items = [line.strip() for line in file_content.splitlines() if line.strip()]

        if not items:
            return jsonify({"success": False, "message": "No valid items found in the file.", "game_state": client_game_state}), 400
        
        new_game_state, action_result_msg, state_actually_changed = apply_game_action({"type": "add_items", "items": items}, client_game_state)
        
        if state_actually_changed:
            new_game_state["chat_log"].append({"sender": "System", "message": f"Action processed: {action_result_msg}"})
        
        return jsonify({
            "success": True,
            "message": f"{len(items)} items uploaded successfully.",
            "game_state": new_game_state,
        })

    except Exception as e:
        print(f"File upload processing error: {e}")
        import traceback
        traceback.print_exc()
        # Ensure that if an error occurs *during* item processing (not state parsing),
        # we return the client_game_state as received.
        return jsonify({"success": False, "message": f"Error processing file: {e}", "game_state": client_game_state}), 500


@app.route('/reset_game', methods=['POST'])
def reset_game():
    # Frontend handles the actual reset of its localStorage state
    # Server just returns a fresh initial state
    return jsonify({
        "success": True,
        "message": "Game state reset.",
        "game_state": DEFAULT_INITIAL_GAME_STATE, # Send the default new state
    })

@app.route('/undo_last_action', methods=['POST'])
def undo_last_action():
    # This action is now entirely client-side.
    # The server simply acknowledges it, but doesn't perform state changes itself.
    # Frontend will manage its own history array.
    # We still need to receive a game_state from the client because Flask expects it for POST,
    # and the client might expect its response to contain a valid game_state.
    client_game_state = get_state_from_request() 
    client_game_state["chat_log"].append({"sender": "System", "message": "The undo operation is performed client-side."})
    return jsonify({
        "success": True,
        "message": "Undo operation acknowledged by server.",
        "game_state": client_game_state # Return current state (which will be overwritten by client's undo)
    })


@app.route('/start_next_auction_action', methods=['POST'])
def start_next_auction_action():
    client_game_state = get_state_from_request()

    if client_game_state["item_list"] and client_game_state["status"] in ["waiting_for_auction_start", "item_sold", "game_over", "waiting_for_items"]:
        if client_game_state["current_item"] and client_game_state["status"] == "bidding":
            narrative = f"Auctioneer: An auction for '{client_game_state['current_item']}' is already in progress. Please bid or sell it first."
            client_game_state["chat_log"].append({"sender": "Auctioneer", "message": narrative})
            return jsonify({"success": False, "message": narrative, "game_state": client_game_state}), 400

        item_to_start = client_game_state["item_list"][0]
        narrative = f"Auctioneer: The auction for '{item_to_start}' is now open! Bids begin at 1 credit."
        game_action = {"type": "start_item_auction", "item": item_to_start}
        
        client_game_state["chat_log"].append({"sender": "Auctioneer", "message": narrative})
        new_game_state, action_result_msg, state_actually_changed = apply_game_action(game_action, client_game_state)
        
        if state_actually_changed:
            new_game_state["chat_log"].append({"sender": "System", "message": f"Action processed: {action_result_msg}"})
        
        return jsonify({"success": True, "game_state": new_game_state})
    else:
        narrative = "Auctioneer: No items available to start an auction, or game not ready. Please add items first, or initialize the game."
        client_game_state["chat_log"].append({"sender": "Auctioneer", "message": narrative})
        return jsonify({"success": False, "message": narrative, "game_state": client_game_state}), 400

@app.route('/sell_current_item_action', methods=['POST'])
def sell_current_item_action():
    client_game_state = get_state_from_request()

    if client_game_state["current_item"] and client_game_state["status"] == "bidding":
        player_name = client_game_state["high_bidder"]
        amount = client_game_state["current_bid"]

        game_action = {"type": "sell_item", "item": client_game_state["current_item"], "player": player_name, "amount": amount}
        
        if player_name and amount > 0:
            narrative = f"Auctioneer: Going once, going twice... Sold! The '{client_game_state['current_item']}' goes to {player_name} for {amount} credits!"
        else:
            narrative = f"Auctioneer: With no bids for '{client_game_state['current_item']}', it is declared unsold!"

        client_game_state["chat_log"].append({"sender": "Auctioneer", "message": narrative})
        new_game_state, action_result_msg, state_actually_changed = apply_game_action(game_action, client_game_state)
        
        if state_actually_changed:
            new_game_state["chat_log"].append({"sender": "System", "message": f"Action processed: {action_result_msg}"})

        return jsonify({"success": True, "game_state": new_game_state})
    else:
        narrative = "Auctioneer: No item currently under auction to sell."
        client_game_state["chat_log"].append({"sender": "Auctioneer", "message": narrative})
        return jsonify({"success": False, "message": narrative, "game_state": client_game_state}), 400

@app.route('/shuffle_items_action', methods=['POST'])
def shuffle_items_action():
    client_game_state = get_state_from_request()

    if client_game_state["item_list"]:
        narrative = "Auctioneer: A little shake-up in the inventory! Items have been reordered."
        client_game_state["chat_log"].append({"sender": "Auctioneer", "message": narrative})

        new_game_state, action_result_msg, state_actually_changed = apply_game_action({"type": "shuffle_items"}, client_game_state)
        
        if state_actually_changed:
            new_game_state["chat_log"].append({"sender": "System", "message": f"Action processed: {action_result_msg}"})
        
        return jsonify({"success": True, "game_state": new_game_state})
    else:
        narrative = "Auctioneer: No items available to shuffle yet."
        client_game_state["chat_log"].append({"sender": "Auctioneer", "message": narrative})
        return jsonify({"success": False, "message": narrative, "game_state": client_game_state}), 400

@app.route('/set_inventory_sort', methods=['POST'])
def set_inventory_sort():
    sort_key = request.json.get('key')
    sort_order = request.json.get('order')
    client_game_state = get_state_from_request()

    if sort_key not in ['name', 'price'] or sort_order not in ['asc', 'desc']:
        return jsonify({"success": False, "message": "Invalid sort key or order.", "game_state": client_game_state}), 400

    new_game_state = copy.deepcopy(client_game_state) # Only for sorting, not a major game action
    new_game_state["player_inventory_sort"] = {"key": sort_key, "order": sort_order}
    new_game_state["chat_log"].append({"sender": "System", "message": f"Player inventories will now be sorted by {sort_key} ({sort_order})."})
    
    action_hash = hash(json.dumps({"type": "set_inventory_sort", "key": sort_key, "order": sort_order}, sort_keys=True))
    new_game_state["last_processed_action_hash"] = action_hash

    return jsonify({"success": True, "game_state": new_game_state})


@app.route('/get_game_state', methods=['GET'])
def get_game_state_route():
    """
    Returns the initial default game state structure. The client will use this
    if it doesn't find a saved state in its localStorage.
    No `history_available` here as history is client-side.
    """
    return jsonify({
        "game_state": DEFAULT_INITIAL_GAME_STATE
    })

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
            --sort-button-color: #555;
            --sort-button-hover: #333;
            --undo-button-color: #FF5722; /* Deep Orange */
            --undo-button-hover: #E64A19;
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
            display: flex;
            justify-content: space-between;
            align-items: center;
        }
        h2 .count {
            font-size: 0.7em;
            font-weight: normal;
            color: #777;
            margin-left: 10px;
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
            grid-template-rows: 1fr; /* Crucial: make the single row take all available height */
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
            box-sizing: border-box;
            overflow-y: auto; 
            overflow-x: hidden; 
            min-height: 0; /* Crucial for flex/grid items */
        }

        /* --- Individual Sections within Panels (.panel-section) --- */
        .panel-section {
            background-color: #fff;
            padding: 15px;
            border-radius: 8px;
            margin-bottom: 20px;
            box-shadow: 0 1px 3px rgba(0,0,0,0.05);
            border: 1px solid var(--border-color);
            flex-shrink: 0; /* Ensures sections take their natural size */
            display: flex; 
            flex-direction: column; 
            overflow: hidden; 
        }
        .panel-section:last-child {
            margin-bottom: 0;
        }

        /* Left Panel specific layout for action buttons/upload */
        .left-panel .panel-section {
            flex-shrink: 0; 
        }
        .left-panel .game-management-section {
            margin-top: auto; 
        }

        /* Specific section styles */
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

        /* --- Item Upload and Reset Button (now within fixed-height sections) --- */
        .item-upload-section, .auction-controls-section, .game-management-section {
            text-align: center;
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
            width: calc(100% - 10px); 
            margin: 5px auto; 
            max-width: 300px;
        }
        .game-management-section button { 
             margin-top: 15px;
        }


        /* --- Scrollable List Wrappers (Content within .panel-section) --- */
        .scrollable-list-wrapper {
            border: 1px solid var(--border-color);
            border-radius: 8px;
            background-color: #fdfdfd;
            padding: 10px;
            box-shadow: inset 0 1px 3px rgba(0,0,0,0.03);
            margin-bottom: 10px; 
            min-width: 0; 
            word-wrap: break-word; 
        }
        .panel-section:last-child .scrollable-list-wrapper {
            margin-bottom: 0; 
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
            margin-left: auto; 
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
        
        /* Inventory Sort and Search Controls */
        .inventory-controls {
            flex-shrink: 0; 
            margin-bottom: 10px; 
        }
        .inventory-controls .search-input {
            width: 100%;
            padding: 10px 12px;
            margin-bottom: 10px;
            border: 1px solid var(--border-color);
            border-radius: 6px;
            font-size: 0.95em;
            box-sizing: border-box; 
            transition: border-color 0.2s ease, box-shadow 0.2s ease;
        }
        .inventory-controls .search-input:focus {
            outline: none;
            border-color: var(--primary-color);
            box-shadow: 0 0 0 3px rgba(76, 175, 80, 0.1);
        }

        .inventory-sort-controls {
            display: flex;
            gap: 5px;
            flex-wrap: wrap; 
            justify-content: center; 
        }
        .inventory-sort-controls button {
            flex: 1;
            min-width: 100px;
            padding: 8px 12px;
            font-size: 0.9em;
            background-color: var(--sort-button-color);
            color: white;
            border-radius: 5px;
            box-shadow: none;
            margin: 2px; 
        }
        .inventory-sort-controls button:hover {
            background-color: var(--sort-button-hover);
            transform: none;
        }
        .inventory-sort-controls button.active {
            background-color: var(--primary-color);
            font-weight: 600;
        }

        /* --- Right Panel (Chat Interface & Command Assistant) --- */
        .right-panel {
            min-height: 500px; /* Ensure chat area has minimum height */
        }
        .chat-log {
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
            min-height: 150px; 
            scroll-behavior: smooth;
            word-wrap: break-word; 
            flex-grow: 1; /* Allow chat log to grow */
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
            background-color: #e8f5e9; 
            align-self: flex-end;
            text-align: right;
            border-bottom-right-radius: 0;
        }
        .chat-message.You strong {
            color: var(--accent-color);
        }
        .chat-message.Auctioneer {
            background-color: #e3f2fd; 
            align-self: flex-start;
            text-align: left;
            border-bottom-left-radius: 0;
        }
        .chat-message.Auctioneer strong {
            color: var(--primary-color);
        }
        .chat-message.System {
            background-color: #fffde7; 
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
        .undo-button {
            background-color: var(--undo-button-color);
            margin-bottom: 10px; /* Space between undo and reset */
        }
        .undo-button:hover {
            background-color: var(--undo-button-hover);
        }


        .command-assistant {
            margin-top: 20px;
            background-color: #f9f9f9;
            padding: 15px;
            border-radius: 8px;
            border: 1px solid #e0e0e0;
            flex-shrink: 0; 
        }
        .command-assistant h3 {
            color: #3f51b5; 
            border-bottom: 1px solid #c5cae9;
            padding-bottom: 8px;
            margin-bottom: 15px;
        }
        .command-assistant ul {
            list-style: none;
            padding: 0;
            margin: 0;
        }
        .command-assistant li {
            margin-bottom: 8px;
            font-size: 0.9em;
            color: #444;
        }
        .command-assistant li strong {
            color: #000;
            font-weight: 500;
        }
        .command-assistant code {
            background-color: #e0e0e0;
            padding: 3px 6px;
            border-radius: 4px;
            font-family: 'Courier New', Courier, monospace;
            font-size: 0.85em;
            color: #c2185b; 
            cursor: pointer; 
            user-select: all; 
            transition: background-color 0.2s ease;
        }
        .command-assistant code:hover {
            background-color: #ccc;
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
                <button id="start-auction-btn" class="auction-control-button" onclick="startNextAuction()" disabled>Auction Next Item</button>
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
                <button id="undo-btn" class="undo-button" onclick="undoLastAction()" disabled>Undo Last Action</button>
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
                <h2>Items Remaining <span id="items-remaining-count" class="count">(0)</span></h2>
                <div id="items-remaining-list-wrapper" class="scrollable-list-wrapper">
                    <ul id="items-remaining-list">
                        <li>No items yet.</li>
                    </ul>
                </div>
            </div>

            <div class="panel-section">
                <h2>Player Inventories</h2>
                <div class="inventory-controls"> <!-- New wrapper for search and sort -->
                    <input type="text" id="inventory-search-input" placeholder="Search items in inventories..." class="search-input">
                    <div class="inventory-sort-controls">
                        <button class="sort-btn active" data-key="name" data-order="asc">Name (A-Z)</button>
                        <button class="sort-btn" data-key="name" data-order="desc">Name (Z-A)</button>
                        <button class="sort-btn" data-key="price" data-order="asc">Price (Low)</button>
                        <button class="sort-btn" data-key="price" data-order="desc">Price (High)</button>
                    </div>
                </div>
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
            
            <div class="command-assistant panel-section">
                <h3>Command Assistant</h3>
                <ul>
                    <li><strong>Start Game:</strong> <code>start game players John, Jane budget 100</code></li>
                    <li><strong>Add Items:</strong> <code>add Car, House, Boat</code></li>
                    <li><strong>Start Auction:</strong> <code>auction Car</code> or <code>auction first</code></li>
                    <li><strong>Place Bid:</b> <code>John bid 10</code> (replace 'John' and '10')</li>
                    <li><strong>Sell Item:</strong> <code>sell it</code> (sells to high bidder) or <code>sell John 50</code></li>
                    <li><strong>No Sale:</b> <code>no sale</code></li>
                    <li><strong>Pass:</b> <code>Jane pass</code> (replace 'Jane')</li>
                    <li><strong>Shuffle:</b> <code>shuffle</code></li>
                </ul>
            </div>
        </div>
    </div>
    <footer>
        Made with &#10084; by Souparna Paul &copy; 2025
    </footer>

    <script>
        const LOCAL_STORAGE_KEY = 'auctionGame';
        const MAX_HISTORY_SIZE = 20; // Client-side undo history limit
        const MAX_CHAT_LOG_FOR_SERVER = 50; // Max chat entries sent to server
        const MAX_AUCTION_HISTORY_FOR_SERVER = 20; // Max auction history entries sent to server


        let currentGameState = {};
        let game_state_history = []; // Client-side history for undo
        let lastKnownChatLogLength = 0; 
        let currentInventorySearchTerm = '';
        // currentInventorySort will be read from currentGameState.player_inventory_sort
        // on UI update, so no separate global needed if it's part of gameState.

        // --- Local Storage Functions ---
        function saveToLocalStorage(state) {
            try {
                localStorage.setItem(LOCAL_STORAGE_KEY, JSON.stringify(state));
            } catch (e) {
                console.error("Error saving to local storage:", e);
                alert("Warning: Could not save game progress to your browser's local storage. Storage might be full.");
            }
        }

        function loadFromLocalStorage() {
            try {
                const serializedState = localStorage.getItem(LOCAL_STORAGE_KEY);
                if (serializedState === null) {
                    return undefined; // No state found
                }
                const loaded = JSON.parse(serializedState);
                // The actual defaultState structure is now fetched once on DOMContentLoaded
                // and used for this merging, as it might evolve.
                return loaded;
            } catch (e) {
                console.error("Error loading from local storage:", e);
                alert("Warning: Saved game data is corrupted. Starting a new game.");
                clearLocalStorage(); // Clear corrupted data
                return undefined; // Corrupted state
            }
        }

        function clearLocalStorage() {
            try {
                localStorage.removeItem(LOCAL_STORAGE_KEY);
            } catch (e) {
                console.error("Error clearing local storage:", e);
            }
        }

        // --- Initialization ---
        document.addEventListener('DOMContentLoaded', () => {
            // Fetch initial default state once to get the structure, then use it for loading/initializing
            fetch('/get_game_state')
                .then(response => {
                    if (!response.ok) throw new Error(`HTTP error! status: ${response.status}`);
                    return response.json();
                })
                .then(data => {
                    const initialDefaultState = data.game_state; // This is the server's default empty state

                    let storedState = loadFromLocalStorage();
                    if (storedState) {
                        // Merge default initial state keys into storedState for forward compatibility
                        for (const key in initialDefaultState) {
                            if (storedState[key] === undefined) {
                                storedState[key] = initialDefaultState[key];
                            }
                        }
                        currentGameState = storedState;
                        console.log("Loading game state from local storage.");
                        // Reconstruct history if possible (not saving game_state_history to localStorage directly)
                        // so undo history will be reset on full page reload.
                    } else {
                        console.log("No state in local storage. Initializing with default state.");
                        currentGameState = initialDefaultState;
                        saveToLocalStorage(currentGameState); // Save this default state
                    }
                    
                    lastKnownChatLogLength = currentGameState.chat_log.length;
                    // Ensure player_inventory_sort is set, even if loaded state didn't have it
                    if (!currentGameState.player_inventory_sort) {
                        currentGameState.player_inventory_sort = { key: 'name', order: 'asc' };
                        saveToLocalStorage(currentGameState); // Persist this new default
                    }
                    updateUI();
                })
                .catch(error => {
                    console.error('Error fetching default game state structure:', error);
                    addChatMessage('System Error', `Could not fetch initial game state structure from server. Please refresh. Details: ${error.message || error}`, 'System');
                });


            document.getElementById('user-message').addEventListener('keypress', function(event) {
                if (event.key === 'Enter') {
                    sendMessage();
                }
            });

            document.querySelectorAll('.inventory-sort-controls .sort-btn').forEach(button => {
                button.addEventListener('click', () => {
                    const key = button.dataset.key;
                    const order = button.dataset.order;
                    setInventorySort(key, order);
                });
            });

            document.getElementById('inventory-search-input').addEventListener('input', function() {
                currentInventorySearchTerm = this.value.toLowerCase().trim();
                updateUI();
            });

            document.querySelectorAll('.command-assistant code').forEach(codeElement => {
                codeElement.addEventListener('click', () => {
                    document.getElementById('user-message').value = codeElement.textContent.trim();
                    document.getElementById('user-message').focus();
                });
            });
        });

        /**
         * Creates a copy of the game state suitable for sending to the server,
         * truncating large arrays like chat_log and auction_history to minimize payload size.
         * The client's local `currentGameState` retains the full history.
         */
        function trimGameStateForServer(state) {
            const trimmedState = JSON.parse(JSON.stringify(state)); // Deep copy to avoid modifying original

            // Truncate chat_log if it's too long
            if (trimmedState.chat_log.length > MAX_CHAT_LOG_FOR_SERVER) {
                trimmedState.chat_log = trimmedState.chat_log.slice(-MAX_CHAT_LOG_FOR_SERVER);
            }

            // Truncate auction_history if it's too long
            if (trimmedState.auction_history.length > MAX_AUCTION_HISTORY_FOR_SERVER) {
                trimmedState.auction_history = trimmedState.auction_history.slice(-MAX_AUCTION_HISTORY_FOR_SERVER);
            }
            return trimmedState;
        }

        /**
         * Merges the server's response game state into the client's local currentGameState.
         * This function assumes the server's `chat_log` and `auction_history` are the
         * most up-to-date (potentially truncated) version, and we replace our local
         * versions with them. This is a trade-off for payload size.
         */
        function mergeServerState(serverState) {
            // Update all fields except chat_log and auction_history directly
            for (const key in serverState) {
                if (key !== 'chat_log' && key !== 'auction_history') {
                    currentGameState[key] = serverState[key];
                }
            }
            
            // For chat_log and auction_history, we replace the local version with the server's.
            // This means older entries (beyond MAX_CHAT_LOG_FOR_SERVER / MAX_AUCTION_HISTORY_FOR_SERVER)
            // will effectively be lost if the server-side processing truncated them.
            currentGameState.chat_log = serverState.chat_log;
            currentGameState.auction_history = serverState.auction_history;

             // Ensure player_items consistency for new players added on server (e.g., via init_game)
            if (currentGameState.participants && currentGameState.player_items) {
                for (const player in currentGameState.participants) {
                    if (currentGameState.player_items[player] === undefined) {
                        currentGameState.player_items[player] = [];
                    }
                }
                // Remove players who might have been removed from participants but still in player_items
                for (const player in currentGameState.player_items) {
                    if (currentGameState.participants[player] === undefined) {
                        delete currentGameState.player_items[player];
                    }
                }
            }
        }


        // --- Generic Send Action Function ---
        async function sendActionToServer(endpoint, payload) {
            try {
                // IMPORTANT: Send a TRIMMED version of game_state to the server
                const trimmedGameState = trimGameStateForServer(currentGameState);
                const fullPayload = { ...payload, game_state: trimmedGameState };
                
                // For /process_chat, add "You" message to local state *before* sending, for immediate feedback
                // and to ensure it's captured in history. Server will also append it to its trimmed log.
                if (endpoint === '/process_chat' && payload.message) {
                    currentGameState.chat_log.push({"sender": "You", "message": payload.message});
                    // lastKnownChatLogLength will be updated by updateUI, or reset if mergeServerState makes it shorter
                    updateUI(); // Immediate UI update for the 'You' message
                }
                
                const response = await fetch(endpoint, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                    },
                    body: JSON.stringify(fullPayload),
                });

                // Check for HTTP errors (4xx, 5xx) before attempting to parse JSON
                if (!response.ok) {
                    const errorText = await response.text();
                    throw new Error(`Server responded with status ${response.status}: ${errorText}`);
                }

                const data = await response.json(); // If this fails, it goes to catch
                
                if (data.success) {
                    // Update client-side game state by merging server's response
                    mergeServerState(data.game_state);
                    saveToLocalStorage(currentGameState); // Persist to local storage
                    pushToHistory(currentGameState); // Only push to history on successful state change
                    updateUI(); // Update UI with server's full response
                } else {
                    alert('Error: ' + data.message); // Display server-side error message
                    // Even if server returns success: false, it might still return an updated game_state
                    // (e.g., chat_log updated with an error message). So, merge if present.
                    if (data.game_state) {
                        mergeServerState(data.game_state);
                        saveToLocalStorage(currentGameState);
                        // No history push if server indicated error and didn't change game state meaningfully
                        updateUI();
                    }
                }
                return data;
            } catch (error) {
                console.error(`Network error during ${endpoint}:`, error);
                alert(`Network error or server issue during ${endpoint}. Please try again. Details: ${error.message || error}`);
                // If a network error occurs, the client's currentGameState might be stale.
                // We don't automatically reset or revert here to avoid data loss.
                // User can manually reset or retry.
                return { success: false, message: `Network error during ${endpoint}.` };
            }
        }

        // --- Action Handlers ---
        async function sendMessage() {
            const userMessageInput = document.getElementById('user-message');
            const message = userMessageInput.value.trim();
            if (!message) return;

            userMessageInput.value = ''; // Clear input field
            // 'You' message is added to local state BEFORE sending for instant feedback
            await sendActionToServer('/process_chat', { message: message });
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
            // IMPORTANT: Send a TRIMMED version of game_state with the file upload
            const trimmedGameState = trimGameStateForServer(currentGameState);
            formData.append('game_state', JSON.stringify(trimmedGameState)); 

            try {
                const response = await fetch('/upload_items', {
                    method: 'POST',
                    body: formData, // FormData automatically sets 'Content-Type': 'multipart/form-data'
                });

                if (!response.ok) {
                    const errorText = await response.text();
                    throw new Error(`Server responded with status ${response.status}: ${errorText}`);
                }

                const data = await response.json();

                if (data.success) {
                    mergeServerState(data.game_state);
                    saveToLocalStorage(currentGameState);
                    pushToHistory(currentGameState); // Only push to history on successful state change
                    updateUI();
                } else {
                    alert('Error uploading items: ' + data.message);
                    if (data.game_state) { // Server might return state even on error
                        mergeServerState(data.game_state);
                        saveToLocalStorage(currentGameState);
                        updateUI();
                    }
                }
            } catch (error) {
                console.error('Error uploading items:', error);
                alert(`Network error or server issue during upload. Details: ${error.message || error}`);
            } finally {
                fileInput.value = '';
            }
        }

        async function resetGame() {
            if (!confirm('Are you sure you want to reset the game? All current progress will be lost.')) {
                return;
            }
            clearLocalStorage();
            game_state_history = []; // Clear client-side history
            
            try {
                const response = await fetch('/reset_game', { // This endpoint now just returns a fresh default
                    method: 'POST', // Use POST for consistency with actions, though it's a GET-like operation conceptually
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ /* No client state needed for server to return default */ })
                });

                if (!response.ok) {
                    const errorText = await response.text();
                    throw new Error(`Server responded with status ${response.status} during reset: ${errorText}`);
                }

                const data = await response.json();
                if (data.success) {
                    currentGameState = data.game_state; // Server sends back the clean initial state
                    saveToLocalStorage(currentGameState);
                    lastKnownChatLogLength = currentGameState.chat_log.length; // Reset chat length tracker
                    updateUI();
                    alert("Game has been reset to its initial state.");
                } else {
                    alert('Failed to reset game: ' + data.message);
                }
            } catch (error) {
                console.error('Error resetting game:', error);
                alert(`Network error during game reset. Please try again. Details: ${error.message || error}`);
                // Fallback: If server is unreachable, just clear locally and use an immediate in-memory default
                // This would require defining a local default in JS. For now, if server is down, it's problematic.
                // Re-fetching the default state at page load will handle this if the server comes back up.
                // For simplicity, we'll let the user retry or refresh.
            }
        }

        function pushToHistory(state) {
            // Only push if the state is actually different from the last history entry
            if (!game_state_history.length || JSON.stringify(state) !== JSON.stringify(game_state_history[game_state_history.length - 1])) {
                game_state_history.push(JSON.parse(JSON.stringify(state))); // Deep copy
                if (game_state_history.length > MAX_HISTORY_SIZE) {
                    game_state_history.shift(); // Remove oldest
                }
            }
        }

        async function undoLastAction() {
            if (game_state_history.length > 0) {
                const previousState = game_state_history.pop();
                currentGameState = previousState;
                saveToLocalStorage(currentGameState);
                alert("Last action has been undone.");
                lastKnownChatLogLength = currentGameState.chat_log.length; // Reset chat length tracker
                updateUI();
            } else {
                alert("No previous state to undo to.");
            }
            // Server doesn't need to know about undo, as it's purely client-side state manipulation.
        }

        async function startNextAuction() {
            await sendActionToServer('/start_next_auction_action', {});
        }

        async function sellCurrentItem() {
            await sendActionToServer('/sell_current_item_action', {});
        }

        async function shuffleItems() {
            await sendActionToServer('/shuffle_items_action', {});
        }

        async function setInventorySort(key, order) {
            // Update local preference first for immediate UI responsiveness
            currentGameState.player_inventory_sort = { key: key, order: order }; 
            await sendActionToServer('/set_inventory_sort', { key: key, order: order });
            // updateUI will be called by sendActionToServer after server confirms
        }

        // --- UI Update Function ---
        function addChatMessage(sender, message, type) {
            const chatLog = document.getElementById('chat-log');
            const div = document.createElement('div');
            div.className = `chat-message ${type}`;
            div.innerHTML = `<strong>${sender}:</strong> ${message}`;
            chatLog.appendChild(div);
            chatLog.scrollTop = chatLog.scrollHeight; 
        }

        function updateUI() {
            // Update Status Message
            const statusMessageDiv = document.getElementById('status-message');
            let statusText = "Game Status: ";
            let statusClass = "info"; 

            if (currentGameState.status === "waiting_for_init") {
                statusText += "Waiting for game initialization.";
                statusClass = "warning";
            } else if (currentGameState.status === "waiting_for_items") {
                statusText += "Game initialized. Waiting for items.";
                statusClass = "warning";
            } else if (currentGameState.status === "waiting_for_auction_start" || currentGameState.status === "item_sold") {
                statusText += "Items added. Ready to start next auction.";
                statusClass = "info"; 
            } else if (currentGameState.status === "bidding") {
                statusText += `Auction for '${currentGameState.current_item}' is active.`;
                statusClass = "success";
            } else if (currentGameState.status === "game_over") {
                statusText += "Game Over - All items processed!";
                statusClass = "error";
            }
            statusMessageDiv.textContent = statusText;
            statusMessageDiv.className = `status-message ${statusClass}`;

            // Update Current Auction Info
            document.getElementById('auction-item').textContent = currentGameState.current_item || 'None';
            document.getElementById('current-bid').textContent = currentGameState.current_bid || 0;
            document.getElementById('high-bidder').textContent = currentGameState.high_bidder || 'None';

            // Enable/Disable Auction Control Buttons
            const startAuctionBtn = document.getElementById('start-auction-btn');
            const sellItemBtn = document.getElementById('sell-item-btn');
            const shuffleItemsBtn = document.getElementById('shuffle-items-btn');
            const undoBtn = document.getElementById('undo-btn');

            startAuctionBtn.disabled = !currentGameState.item_list.length || currentGameState.status === "bidding" || currentGameState.status === "game_over";
            sellItemBtn.disabled = !currentGameState.current_item || currentGameState.status !== "bidding";
            shuffleItemsBtn.disabled = !currentGameState.item_list.length;
            undoBtn.disabled = game_state_history.length === 0;


            // Update Participants List
            const participantsList = document.getElementById('participants-list');
            participantsList.innerHTML = '';
            if (Object.keys(currentGameState.participants).length === 0) {
                participantsList.innerHTML = '<li>No participants yet.</li>';
            } else {
                for (const player in currentGameState.participants) {
                    const li = document.createElement('li');
                    // Ensure player_items key exists for the player to prevent error
                    const ownedItemsCount = currentGameState.player_items[player] ? currentGameState.player_items[player].length : 0;
                    li.innerHTML = `<span>${player}</span> 
                                    <span class="player-budget">${currentGameState.participants[player]} credits</span>
                                    <span class="player-item-count">(${ownedItemsCount} items)</span>`;
                    participantsList.appendChild(li);
                }
            }

            // Update Items Remaining List & Count
            const itemsRemainingList = document.getElementById('items-remaining-list');
            const itemsRemainingCountSpan = document.getElementById('items-remaining-count');
            itemsRemainingList.innerHTML = '';
            
            let displayItems = [...currentGameState.item_list];
            if (currentGameState.current_item && currentGameState.status === "bidding") {
                const currentItemIndex = displayItems.indexOf(currentGameState.current_item);
                if (currentItemIndex > -1) {
                    displayItems.splice(currentItemIndex, 1); 
                }
                displayItems.unshift(currentGameState.current_item + " (current)"); 
            }

            itemsRemainingCountSpan.textContent = `(${currentGameState.item_list.length})`; 

            if (displayItems.length === 0) {
                itemsRemainingList.innerHTML = '<li>No items remaining.</li>';
            } else {
                displayItems.forEach(item => {
                    const li = document.createElement('li');
                    li.textContent = item;
                    itemsRemainingList.appendChild(li);
                });
            }


            // Update Player Inventories List - NOW SHOWS PRICE & SORTED & FILTERED
            const playerInventoriesList = document.getElementById('player-inventories-list');
            playerInventoriesList.innerHTML = '';
            let hasItemsBought = false;

            // Update active sort buttons based on currentGameState.player_inventory_sort
            document.querySelectorAll('.inventory-sort-controls .sort-btn').forEach(button => {
                const key = button.dataset.key;
                const order = button.dataset.order;
                if (currentGameState.player_inventory_sort && key === currentGameState.player_inventory_sort.key && order === currentGameState.player_inventory_sort.order) {
                    button.classList.add('active');
                } else {
                    button.classList.remove('active');
                }
            });

            for (const player in currentGameState.player_items) {
                const filteredItems = currentGameState.player_items[player].filter(item => 
                    item.name.toLowerCase().includes(currentInventorySearchTerm)
                );

                const playerItems = [...filteredItems]; 
                
                playerItems.sort((a, b) => {
                    let compareA, compareB;
                    // Use currentGameState.player_inventory_sort here
                    const sortKey = currentGameState.player_inventory_sort.key;
                    const sortOrder = currentGameState.player_inventory_sort.order;

                    if (sortKey === 'name') {
                        compareA = a.name.toLowerCase();
                        compareB = b.name.toLowerCase();
                    } else { // 'price'
                        compareA = a.price;
                        compareB = b.price;
                    }

                    if (compareA < compareB) return sortOrder === 'asc' ? -1 : 1;
                    if (compareA > compareB) return sortOrder === 'asc' ? 1 : -1;
                    return 0; 
                });

                if (playerItems.length > 0) {
                    hasItemsBought = true;
                    const headerLi = document.createElement('li');
                    headerLi.className = 'player-inventory-header';
                    headerLi.textContent = `${player}'s Inventory`;
                    playerInventoriesList.appendChild(headerLi);
                    playerItems.forEach(item => {
                        const itemLi = document.createElement('li');
                        itemLi.className = 'player-inventory-item';
                        itemLi.innerHTML = `${item.name} <span class="item-price">(${item.price} credits)</span>`; 
                        playerInventoriesList.appendChild(itemLi);
                    });
                }
            }
            if (!hasItemsBought && !currentInventorySearchTerm) { 
                playerInventoriesList.innerHTML = '<li>No items purchased yet.</li>';
            } else if (!hasItemsBought && currentInventorySearchTerm) {
                 playerInventoriesList.innerHTML = `<li>No items found matching "${currentInventorySearchTerm}".</li>`;
            }


            // Update Auction History
            const auctionHistoryList = document.getElementById('auction-history-list');
            auctionHistoryList.innerHTML = '';
            // Display full local history, not just server's potentially trimmed version
            if (currentGameState.auction_history.length === 0) {
                auctionHistoryList.innerHTML = '<li>No items sold yet.</li>';
            } else {
                // Display in reverse order (most recent first)
                [...currentGameState.auction_history].reverse().forEach(entry => {
                    const li = document.createElement('li');
                    li.textContent = entry;
                    auctionHistoryList.appendChild(li);
                });
            }

            // Incremental Chat Log Update Logic (retained)
            const chatLogDiv = document.getElementById('chat-log');
            if (currentGameState.chat_log.length < lastKnownChatLogLength) {
                chatLogDiv.innerHTML = '';
                for (const chatEntry of currentGameState.chat_log) {
                    addChatMessage(chatEntry.sender, chatEntry.message, chatEntry.sender);
                }
            } else if (currentGameState.chat_log.length > lastKnownChatLogLength) {
                for (let i = lastKnownChatLogLength; i < currentGameState.chat_log.length; i++) {
                    const chatEntry = currentGameState.chat_log[i];
                    addChatMessage(chatEntry.sender, chatEntry.message, chatEntry.sender);
                }
            }
            lastKnownChatLogLength = currentGameState.chat_log.length; // Update the last known length
            chatLogDiv.scrollTop = chatLogDiv.scrollHeight; // Scroll to bottom for latest messages
        }
    </script>
</body>
</html>
"""

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=True)