const express = require('express');
const http = require('http');
const WebSocket = require('ws');
const path = require('path');
const dotenv = require('dotenv');

// Load environment variables from .env file
dotenv.config();

// Access the API key securely
const GEMINI_API_KEY = process.env.GEMINI_API_KEY;
// Corrected model name to a stable 'flash' variant.
const GEMINI_MODEL_NAME = "gemini-2.5-flash-preview-05-20";

let uuidv4;
let GoogleGenerativeAI;

(async () => {
    try {
        const uuidModule = await import('uuid');
        uuidv4 = uuidModule.v4;

        const generativeAIModule = await import('@google/generative-ai');
        GoogleGenerativeAI = generativeAIModule.GoogleGenerativeAI;

        if (!GEMINI_API_KEY) {
            console.warn("GEMINI_API_KEY is not set. Please set it in your .env file or environment variables.");
            // Do NOT exit here, as the game can still run without AI.
            // Just ensure model is null so AI functions are skipped.
        }

    } catch (error) {
        console.error("Failed to load critical modules or environment variables:");
        console.error(error);
        process.exit(1);
    }

    const app = express();
    const server = http.createServer(app);
    const wss = new WebSocket.Server({ server });

    const PORT = process.env.PORT || 3000;

    app.use(express.static(__dirname));

    const gameRooms = {};

    const DEFAULT_GAME_SETTINGS = {
        playerStartingBudget: 5000,
        minBidIncrementPercentage: 5,
        auctionRoundDuration: 15
    };

    // --- Helper Functions ---

    function generateRoomId() {
        let roomId;
        do {
            roomId = Math.random().toString(36).substring(2, 6).toUpperCase();
        } while (gameRooms[roomId]);
        return roomId;
    }

    function createNewGameState() {
        return {
            items: [],
            players: {}, // Stores player objects, including their WebSocket (ws) reference
            currentAuctionItem: null,
            currentHighestBid: 0,
            currentHighestBidder: null,
            auctionState: 'idle',
            timer: {
                active: false,
                endTime: 0,
                interval: null // This stores the Node.js Timeout object, which is circular
            },
            gameSettings: { ...DEFAULT_GAME_SETTINGS }
        };
    }

    // Helper to create a clean state object for broadcasting/sending to clients,
    // removing circular references and server-only data.
    function getCleanGameStateForClient(gameState) {
        // Explicitly build a new object with only serializable data
        const cleanState = {
            items: gameState.items.map(item => ({ ...item })), // Deep copy items
            currentAuctionItem: gameState.currentAuctionItem ? { ...gameState.currentAuctionItem } : null,
            currentHighestBid: gameState.currentHighestBid,
            currentHighestBidder: gameState.currentHighestBidder,
            auctionState: gameState.auctionState,
            gameSettings: { ...gameState.gameSettings }, // Shallow copy settings
            // Timer only needs active and endTime, not the circular interval object
            timer: {
                active: gameState.timer.active,
                endTime: gameState.timer.endTime
            },
            // Players need special handling to exclude the WebSocket object reference
            players: {}
        };

        for (const playerId in gameState.players) {
            const player = gameState.players[playerId];
            cleanState.players[playerId] = {
                playerId: playerId, // Include playerId for client-side identification
                name: player.name,
                budget: player.budget,
                wonItems: player.wonItems.map(item => ({ ...item })), // Deep copy wonItems
                // Explicitly EXCLUDE the 'ws' (WebSocket) object
            };
        }

        return cleanState;
    }

    function broadcastToRoom(roomId, message) {
        if (!gameRooms[roomId]) return;

        // If the message contains gameState, sanitize it before sending
        let messageToSend = { ...message }; // Shallow copy of the message object
        if (messageToSend.state) {
            messageToSend.state = getCleanGameStateForClient(messageToSend.state);
        } else if (messageToSend.auctionState) {
            messageToSend.auctionState = getCleanGameStateForClient(messageToSend.auctionState);
        }

        wss.clients.forEach(client => {
            if (client.readyState === WebSocket.OPEN && client.roomId === roomId) {
                client.send(JSON.stringify(messageToSend));
            }
        });
    }

    function sendToClient(ws, message) {
        if (ws && ws.readyState === WebSocket.OPEN) {
            // If the message contains gameState, sanitize it before sending
            let messageToSend = { ...message }; // Shallow copy of the message object
            if (messageToSend.state) {
                messageToSend.state = getCleanGameStateForClient(messageToSend.state);
            } else if (messageToSend.auctionState) {
                messageToSend.auctionState = getCleanGameStateForClient(messageToSend.auctionState);
            }
            ws.send(JSON.stringify(messageToSend));
        }
    }

    function startAuctionTimer(roomId) {
        const room = gameRooms[roomId];
        if (!room) return;

        if (room.gameState.timer.interval) {
            clearInterval(room.gameState.timer.interval);
        }
        room.gameState.timer.endTime = Date.now() + (room.gameState.gameSettings.auctionRoundDuration * 1000);
        room.gameState.timer.active = true;

        room.gameState.timer.interval = setInterval(() => {
            const remainingTime = Math.max(0, Math.floor((room.gameState.timer.endTime - Date.now()) / 1000));
            if (remainingTime <= 0) {
                clearInterval(room.gameState.timer.interval);
                room.gameState.timer.active = false;
                room.gameState.timer.interval = null;
                console.log(`Room ${roomId}: Auction timer ran out. Finalizing item...`);
                finalizeCurrentAuction(roomId, true);
            }
            broadcastToRoom(roomId, { type: 'auction_state_update', state: room.gameState });
        }, 1000);
    }

    function resetAuctionTimer(roomId) {
        const room = gameRooms[roomId];
        if (!room) return;
        if (room.gameState.auctionState === 'bidding') {
            clearInterval(room.gameState.timer.interval);
            startAuctionTimer(roomId);
        }
    }

    function stopAuctionTimer(roomId) {
        const room = gameRooms[roomId];
        if (!room) return;
        if (room.gameState.timer.interval) {
            clearInterval(room.gameState.timer.interval);
            room.gameState.timer.interval = null;
        }
        room.gameState.timer.active = false;
        room.gameState.timer.endTime = 0;
    }

    function finalizeCurrentAuction(roomId, byTimer = false) {
        const room = gameRooms[roomId];
        if (!room) return;
        if (room.gameState.auctionState !== 'bidding' || !room.gameState.currentAuctionItem) {
            return;
        }
        stopAuctionTimer(roomId);

        const wonItem = room.gameState.currentAuctionItem;
        const finalBid = room.gameState.currentHighestBid;
        const winnerId = room.gameState.currentHighestBidder;
        const winnerName = room.gameState.players[winnerId]?.name;

        if (winnerId && room.gameState.players[winnerId]) {
            room.gameState.players[winnerId].wonItems.push({ ...wonItem, finalBid });
            const itemInList = room.gameState.items.find(item => item.id === wonItem.id);
            if (itemInList) itemInList.status = 'sold';

            broadcastToRoom(roomId, {
                type: 'item_finalized',
                item: wonItem,
                finalBid: finalBid,
                winnerId: winnerId,
                winnerName: winnerName,
                auctionState: room.gameState // Will be sanitized by broadcastToRoom
            });
            console.log(`Room ${roomId}: Item "${wonItem.name}" sold to ${winnerName || winnerId.substring(0,8)} for $${finalBid.toLocaleString()}`);
        } else {
            const itemInList = room.gameState.items.find(item => item.id === wonItem.id);
            if (itemInList) itemInList.status = 'pending';

            broadcastToRoom(roomId, {
                type: 'info',
                message: byTimer ? `Time's up! "${wonItem.name}" finalized with no winner (no bids). Item returned to pending.`
                                : `"${wonItem.name}" finalized with no winner (no bids). Item returned to pending.`
            });
            console.log(`Room ${roomId}: Item "${wonItem.name}" had no bids and was not sold.`);
        }

        room.gameState.currentAuctionItem = null;
        room.gameState.currentHighestBid = 0;
        room.gameState.currentHighestBidder = null;
        room.gameState.auctionState = 'idle';
        broadcastToRoom(roomId, { type: 'auction_state_update', state: room.gameState });
    }

    // --- LLM Integration Function ---
    const genAI = GEMINI_API_KEY ? new GoogleGenerativeAI(GEMINI_API_KEY) : null;
    const model = genAI ? genAI.getGenerativeModel({ model: GEMINI_MODEL_NAME }) : null;

    async function handleLlmQuery(query, role, roomId) {
        if (!model) {
            console.warn("Gemini model not initialized. API key might be missing.");
            return `(AI Helper for ${role}): AI is currently unavailable. Please check the server configuration (API key).`;
        }

        const room = gameRooms[roomId];
        const currentGameState = room ? room.gameState : null;

        let gameContext = `You are an AI assistant for an online auction game. The current room ID is ${roomId}.`;
        gameContext += `\nYour role: ${role}.`;

        if (currentGameState) {
            gameContext += `\nGame Settings: Player Starting Budget = $${currentGameState.gameSettings.playerStartingBudget.toLocaleString()}, Min Bid Increment = ${currentGameState.gameSettings.minBidIncrementPercentage}%, Auction Round Duration = ${currentGameState.gameSettings.auctionRoundDuration} seconds.`;
            gameContext += `\nAuction State: ${currentGameState.auctionState}.`;
            if (currentGameState.currentAuctionItem) {
                gameContext += `\nCurrently Auctioning: "${currentGameState.currentAuctionItem.name}" (Base Price: $${currentGameState.currentAuctionItem.basePrice.toLocaleString()}).`;
                gameContext += `\nCurrent Highest Bid: $${currentGameState.currentHighestBid.toLocaleString()}.`;
                if (currentGameState.currentHighestBidder) {
                    const bidderName = currentGameState.players[currentGameState.currentHighestBidder]?.name || currentGameState.currentHighestBidder.substring(0,8);
                    gameContext += `\nHighest Bidder: ${bidderName}.`;
                } else {
                    gameContext += `\nNo bids placed yet.`;
                }
            } else {
                gameContext += `\nNo item currently selected for auction.`;
            }
            const activePlayers = Object.values(currentGameState.players).filter(p => p.ws && p.ws.readyState === WebSocket.OPEN);
            if (activePlayers.length > 0) {
                 gameContext += `\nConnected Players: ${activePlayers.map(p => p.name).join(', ')}.`;
            }
        }

        const prompt = `
        ${gameContext}

        The user is asking: "${query}"

        Based on the game context, provide a helpful and concise response. If the question is about rules, explain the relevant rule. If it's about the current item, describe its status. If it's a general question not covered by specific game rules, provide a helpful general answer. If it asks about a specific player's budget, state that you do not have access to individual player budgets. Keep your response brief and to the point, acting as a supportive game assistant.
        `;

        try {
            const result = await model.generateContent(prompt);
            const response = await result.response;
            const text = response.text();
            return text;
        } catch (error) {
            console.error("Error calling Gemini API:", error);
            return `(AI Helper for ${role}): I apologize, I'm having trouble connecting to my knowledge base right now. Please try again later. (Error: ${error.message})`;
        }
    }


    // --- WebSocket Connection Handling ---
    wss.on('connection', ws => {
        ws.id = uuidv4(); // Unique ID for this WebSocket connection
        ws.roomId = null;
        ws.playerId = null; // In-game player ID (uuidv4 for the game entity)
        ws.role = 'guest';
        console.log(`Client ${ws.id} connected`);

        sendToClient(ws, { type: 'client_id_assigned', id: ws.id });

        ws.on('message', async message => {
            const data = JSON.parse(message);
            console.log(`Received from ${ws.id} (Role: ${ws.role}, Room: ${ws.roomId}, PlayerId: ${ws.playerId}):`, data);

            switch (data.type) {
                case 'create_room':
                    if (ws.roomId) return sendToClient(ws, { type: 'error', message: 'You are already in a room. Leave first to create a new one.' });

                    const newRoomId = generateRoomId();
                    ws.playerId = uuidv4(); // Generate player ID for auctioneer
                    gameRooms[newRoomId] = {
                        gameState: createNewGameState(),
                        auctioneerPlayerId: ws.playerId, // Stores the auctioneer's in-game ID
                        activeAuctioneerWsId: ws.id,    // Stores the active WebSocket ID
                    };
                    ws.roomId = newRoomId;
                    ws.role = 'auctioneer';

                    const newAuctioneerPlayer = {
                        playerId: ws.playerId,
                        name: `Auctioneer ${ws.playerId.substring(0,8)}`,
                        budget: gameRooms[newRoomId].gameState.gameSettings.playerStartingBudget,
                        wonItems: [],
                        ws: ws
                    };
                    gameRooms[newRoomId].gameState.players[ws.playerId] = newAuctioneerPlayer;

                    sendToClient(ws, { type: 'room_created', roomId: newRoomId, playerId: ws.playerId }); // Send player ID to client
                    sendToClient(ws, { type: 'joined_room', roomId: newRoomId, role: 'auctioneer', playerId: ws.playerId, playerName: newAuctioneerPlayer.name, budget: newAuctioneerPlayer.budget });
                    broadcastToRoom(newRoomId, { type: 'info', message: `Auction Room "${newRoomId}" created by Auctioneer ${newAuctioneerPlayer.name}.` });
                    broadcastToRoom(newRoomId, { type: 'auction_state_update', state: gameRooms[newRoomId].gameState });
                    console.log(`Room ${newRoomId} created by ${ws.id} (Player ID: ${ws.playerId})`);
                    return;

                case 'join_room':
                    if (ws.roomId) return sendToClient(ws, { type: 'error', message: 'You are already in a room.' });
                    const targetRoomId = data.roomId?.toUpperCase();
                    if (!targetRoomId || !gameRooms[targetRoomId]) {
                        return sendToClient(ws, { type: 'room_not_found', message: `Room "${targetRoomId}" not found.` });
                    }

                    ws.roomId = targetRoomId;
                    ws.role = 'player';
                    ws.playerId = uuidv4(); // Generate player ID for regular player

                    const newPlayer = {
                        playerId: ws.playerId,
                        name: data.playerName || `Player ${ws.playerId.substring(0,8)}`,
                        budget: gameRooms[targetRoomId].gameState.gameSettings.playerStartingBudget,
                        wonItems: [],
                        ws: ws
                    };
                    gameRooms[targetRoomId].gameState.players[ws.playerId] = newPlayer;

                    sendToClient(ws, { type: 'joined_room', roomId: targetRoomId, role: 'player', playerId: ws.playerId, playerName: newPlayer.name, budget: newPlayer.budget });
                    broadcastToRoom(targetRoomId, { type: 'info', message: `${newPlayer.name} joined the game.` });
                    broadcastToRoom(targetRoomId, { type: 'auction_state_update', state: gameRooms[targetRoomId].gameState });
                    console.log(`Client ${ws.id} joined room ${targetRoomId} as player ${ws.playerId}`);
                    return;

                case 'reconnect_session':
                    if (data.roomId && gameRooms[data.roomId]) {
                        const room = gameRooms[data.roomId];
                        // Reconnect Auctioneer
                        if (data.role === 'auctioneer' && room.auctioneerPlayerId === data.playerId) {
                            ws.roomId = data.roomId;
                            ws.role = 'auctioneer';
                            ws.playerId = data.playerId; // Ensure WS object has correct player ID
                            if (room.gameState.players[ws.playerId]) {
                                room.gameState.players[ws.playerId].ws = ws; // Update player's WS reference
                                room.activeAuctioneerWsId = ws.id; // Update room's active auctioneer WS ID
                                sendToClient(ws, {
                                    type: 'reconnected_session',
                                    roomId: ws.roomId,
                                    role: 'auctioneer',
                                    playerId: ws.playerId,
                                    playerName: room.gameState.players[ws.playerId].name,
                                    budget: room.gameState.players[ws.playerId].budget
                                });
                                broadcastToRoom(ws.roomId, { type: 'info', message: `Auctioneer ${room.gameState.players[ws.playerId].name} reconnected.` });
                                broadcastToRoom(ws.roomId, { type: 'auction_state_update', state: room.gameState });
                                console.log(`Client ${ws.id} reconnected to room ${ws.roomId} as AUCTIONEER (Player ID: ${ws.playerId}).`);
                            } else {
                                console.log(`Reconnect failed: Auctioneer player data missing for ${data.playerId}.`);
                                sendToClient(ws, { type: 'error', message: 'Reconnect failed: Auctioneer session data corrupted. Please create a new room.' });
                            }
                        }
                        // Reconnect Regular Player
                        else if (data.role === 'player' && room.gameState.players[data.playerId]) {
                            ws.roomId = data.roomId;
                            ws.role = 'player';
                            ws.playerId = data.playerId; // Ensure WS object has correct player ID
                            room.gameState.players[ws.playerId].ws = ws; // Update player's WS reference
                            if (data.playerName) room.gameState.players[ws.playerId].name = data.playerName;
                            sendToClient(ws, {
                                type: 'reconnected_session',
                                roomId: ws.roomId,
                                role: 'player',
                                playerId: ws.playerId,
                                playerName: room.gameState.players[ws.playerId].name,
                                budget: room.gameState.players[ws.playerId].budget
                            });
                            broadcastToRoom(ws.roomId, { type: 'info', message: `Player ${room.gameState.players[ws.playerId].name} reconnected.` });
                            broadcastToRoom(ws.roomId, { type: 'auction_state_update', state: room.gameState });
                            console.log(`Client ${ws.id} reconnected to room ${ws.roomId} as PLAYER (Player ID: ${ws.playerId}).`);
                        } else {
                             sendToClient(ws, { type: 'error', message: 'Reconnect failed. Session data mismatch or invalid.' });
                             console.log(`Client ${ws.id} failed to reconnect to room ${data.roomId} with Player ID ${data.playerId}.`);
                        }
                    } else {
                        sendToClient(ws, { type: 'error', message: 'Reconnect failed. Room not found or invalid session.' });
                        console.log(`Client ${ws.id} failed to reconnect to room ${data.roomId}.`);
                    }
                    return;
            }

            if (!ws.roomId || !gameRooms[ws.roomId]) {
                return sendToClient(ws, { type: 'error', message: 'You must be in a room to perform this action.' });
            }
            const currentRoom = gameRooms[ws.roomId];
            const currentGameState = currentRoom.gameState;

            switch (data.type) {
                case 'set_player_name':
                    if (ws.playerId !== data.playerId || !currentGameState.players[data.playerId]) {
                        return sendToClient(ws, { type: 'error', message: 'Invalid player for this action.' });
                    }
                    if (!data.name || data.name.trim() === '') {
                        return sendToClient(ws, { type: 'error', message: 'Player name cannot be empty.' });
                    }
                    currentGameState.players[data.playerId].name = data.name.trim().substring(0, 20);
                    broadcastToRoom(ws.roomId, { type: 'auction_state_update', state: currentGameState });
                    sendToClient(ws, { type: 'info', message: `Your display name is now "${currentGameState.players[data.playerId].name}".` });
                    break;

                case 'update_settings':
                    if (ws.playerId !== currentRoom.auctioneerPlayerId) return sendToClient(ws, { type: 'error', message: 'Only the auctioneer can update game settings.' });

                    const { playerStartingBudget, minBidIncrementPercentage, auctionRoundDuration } = data.settings;

                    if (isNaN(playerStartingBudget) || playerStartingBudget < 100 ||
                        isNaN(minBidIncrementPercentage) || minBidIncrementPercentage < 1 || minBidIncrementPercentage > 100 ||
                        isNaN(auctionRoundDuration) || auctionRoundDuration < 5 || auctionRoundDuration > 120) {
                        return sendToClient(ws, { type: 'error', message: 'Invalid setting values. Budget (min 100), Increment (1-100%), Duration (5-120s).' });
                    }

                    currentGameState.gameSettings.playerStartingBudget = playerStartingBudget;
                    currentGameState.gameSettings.minBidIncrementPercentage = minBidIncrementPercentage;
                    currentGameState.gameSettings.auctionRoundDuration = auctionRoundDuration;

                    broadcastToRoom(ws.roomId, { type: 'settings_updated', settings: currentGameState.gameSettings });
                    broadcastToRoom(ws.roomId, { type: 'auction_state_update', state: currentGameState });
                    console.log(`Room ${ws.roomId}: Game settings updated:`, currentGameState.gameSettings);
                    break;

                case 'add_item':
                    if (ws.playerId !== currentRoom.auctioneerPlayerId) return sendToClient(ws, { type: 'error', message: 'Only the auctioneer can add items.' });
                    if (!data.name || isNaN(data.basePrice) || data.basePrice < 0) return sendToClient(ws, { type: 'error', message: 'Invalid item details.' });

                    const newItem = {
                        id: uuidv4(),
                        name: data.name,
                        basePrice: parseFloat(data.basePrice),
                        status: 'pending'
                    };
                    currentGameState.items.push(newItem);
                    broadcastToRoom(ws.roomId, { type: 'item_added', item: newItem, items: currentGameState.items });
                    broadcastToRoom(ws.roomId, { type: 'auction_state_update', state: currentGameState });
                    break;

                case 'add_batch_items':
                    if (ws.playerId !== currentRoom.auctioneerPlayerId) return sendToClient(ws, { type: 'error', message: 'Only the auctioneer can add items in batch.' });
                    if (!data.items || !Array.isArray(data.items) || data.items.length === 0) return sendToClient(ws, { type: 'error', message: 'No valid items provided for batch inclusion.' });

                    let addedCount = 0;
                    data.items.forEach(itemData => {
                        const name = itemData.name;
                        const basePrice = parseFloat(itemData.basePrice);
                        if (name && !isNaN(basePrice) && basePrice >= 0) {
                            const newItem = {
                                id: uuidv4(),
                                name: name,
                                basePrice: basePrice,
                                status: 'pending'
                            };
                            currentGameState.items.push(newItem);
                            addedCount++;
                        }
                    });
                    broadcastToRoom(ws.roomId, { type: 'batch_items_added', count: addedCount, items: currentGameState.items });
                    broadcastToRoom(ws.roomId, { type: 'auction_state_update', state: currentGameState });
                    break;

                case 'select_item_for_auction':
                    if (ws.playerId !== currentRoom.auctioneerPlayerId) return sendToClient(ws, { type: 'error', message: 'Only the auctioneer can select items.' });
                    if (currentGameState.auctionState !== 'idle') {
                        return sendToClient(ws, { type: 'error', message: 'An auction is already in progress. Finalize or clear it first.' });
                    }

                    const itemToAuction = currentGameState.items.find(item => item.id === data.itemId && item.status === 'pending');
                    if (itemToAuction) {
                        currentGameState.currentAuctionItem = itemToAuction;
                        currentGameState.currentHighestBid = itemToAuction.basePrice;
                        currentGameState.currentHighestBidder = null;
                        currentGameState.auctionState = 'item_selected';
                        itemToAuction.status = 'auctioning';
                        broadcastToRoom(ws.roomId, { type: 'auction_state_update', state: currentGameState });
                        broadcastToRoom(ws.roomId, { type: 'info', message: `Auctioneer selected "${itemToAuction.name}" for auction. Base price: $${itemToAuction.basePrice.toLocaleString()}` });
                    } else {
                        sendToClient(ws, { type: 'error', message: 'Item not found or already auctioned/selected.' });
                    }
                    break;

                case 'start_bidding':
                    if (ws.playerId !== currentRoom.auctioneerPlayerId) return sendToClient(ws, { type: 'error', message: 'Only the auctioneer can start bidding.' });
                    if (currentGameState.auctionState !== 'item_selected' || !currentGameState.currentAuctionItem) {
                        return sendToClient(ws, { type: 'error', message: 'No item selected or bidding already started.' });
                    }
                    currentGameState.auctionState = 'bidding';
                    startAuctionTimer(ws.roomId);
                    broadcastToRoom(ws.roomId, { type: 'auction_state_update', state: currentGameState });
                    broadcastToRoom(ws.roomId, { type: 'info', message: `Bidding started for "${currentGameState.currentAuctionItem.name}"!` });
                    break;

                case 'finalize_item':
                    if (ws.playerId !== currentRoom.auctioneerPlayerId) return sendToClient(ws, { type: 'error', message: 'Only the auctioneer can finalize items.' });
                    finalizeCurrentAuction(ws.roomId, false);
                    break;

                case 'clear_auction':
                    if (ws.playerId !== currentRoom.auctioneerPlayerId) return sendToClient(ws, { type: 'error', message: 'Only the auctioneer can clear the auction.' });

                    stopAuctionTimer(ws.roomId);

                    if (currentGameState.currentAuctionItem) {
                        const itemInList = currentGameState.items.find(item => item.id === currentGameState.currentAuctionItem.id);
                        if (itemInList && itemInList.status === 'auctioning') {
                            itemInList.status = 'pending';
                        }
                        if (currentGameState.currentHighestBidder && currentGameState.players[currentGameState.currentHighestBidder] && currentGameState.auctionState === 'bidding') {
                             const player = currentGameState.players[currentGameState.currentHighestBidder];
                            if (player.ws && player.ws.readyState === WebSocket.OPEN) {
                                player.budget += currentGameState.currentHighestBid;
                                sendToClient(player.ws, {
                                    type: 'player_bid_update',
                                    playerBudget: player.budget,
                                    message: `Auction for "${currentGameState.currentAuctionItem.name}" cleared. Your bid refunded.`,
                                    success: true
                                });
                            }
                        }
                    }
                    currentGameState.currentAuctionItem = null;
                    currentGameState.currentHighestBid = 0;
                    currentGameState.currentHighestBidder = null;
                    currentGameState.auctionState = 'idle';
                    broadcastToRoom(ws.roomId, { type: 'auction_cleared', auctionState: currentGameState });
                    break;

                case 'place_bid':
                    if (ws.playerId !== data.playerId || !currentGameState.players[data.playerId]) return sendToClient(ws, { type: 'error', message: 'Invalid player or action.' });
                    if (currentGameState.auctionState !== 'bidding' || !currentGameState.currentAuctionItem) {
                        return sendToClient(ws, { type: 'error', message: 'Bidding is not active for an item.' });
                    }

                    const player = currentGameState.players[data.playerId];
                    const bid = parseFloat(data.bidAmount);
                    const minAllowedBid = Math.ceil(currentGameState.currentHighestBid * (1 + currentGameState.gameSettings.minBidIncrementPercentage / 100));

                    if (isNaN(bid) || bid < minAllowedBid) {
                        return sendToClient(ws, { type: 'error', message: `Bid must be at least $${minAllowedBid.toLocaleString()}.` });
                    }
                    if (bid > player.budget) {
                        return sendToClient(ws, { type: 'error', message: `Bid of $${bid.toLocaleString()} exceeds your budget of $${player.budget.toLocaleString()}.` });
                    }

                    if (currentGameState.currentHighestBidder && currentGameState.players[currentGameState.currentHighestBidder] && currentGameState.currentHighestBidder !== ws.playerId) {
                        const prevHighestBidder = currentGameState.players[currentGameState.currentHighestBidder];
                        if (prevHighestBidder.ws && prevHighestBidder.ws.readyState === WebSocket.OPEN) {
                            prevHighestBidder.budget += currentGameState.currentHighestBid;
                            sendToClient(prevHighestBidder.ws, {
                                type: 'player_bid_update',
                                playerBudget: prevHighestBidder.budget,
                                message: `Your bid of $${currentGameState.currentHighestBid.toLocaleString()} for "${currentGameState.currentAuctionItem.name}" was outbid. Budget refunded.`,
                                success: true
                            });
                        }
                    }

                    player.budget -= bid;
                    currentGameState.currentHighestBid = bid;
                    currentGameState.currentHighestBidder = data.playerId;

                    resetAuctionTimer(ws.roomId);

                    broadcastToRoom(ws.roomId, {
                        type: 'player_bid_update',
                        auctionState: currentGameState,
                        playerId: ws.playerId,
                        playerBudget: player.budget,
                        message: `${player.name} bid $${bid.toLocaleString()} for "${currentGameState.currentAuctionItem.name}".`,
                        success: true
                    });
                    break;

                case 'llm_query':
                    const llmResponse = await handleLlmQuery(data.query, data.role, data.roomId);
                    sendToClient(ws, {
                        type: 'llm_response',
                        response: llmResponse,
                        clientId: ws.id
                    });
                    break;

                default:
                    sendToClient(ws, { type: 'error', message: 'Unknown message type.' });
                    break;
            }
        });

        ws.on('close', () => {
            console.log(`Client ${ws.id} disconnected (Room: ${ws.roomId}, Role: ${ws.role}, PlayerId: ${ws.playerId})`);

            if (!ws.roomId || !gameRooms[ws.roomId]) {
                return;
            }

            const room = gameRooms[ws.roomId];
            const currentGameState = room.gameState;

            // Check if the disconnected client was the Auctioneer
            if (ws.playerId === room.auctioneerPlayerId) {
                room.activeAuctioneerWsId = null; // Clear the active WS ID for this auctioneer
                broadcastToRoom(ws.roomId, { type: 'info', message: 'Auctioneer disconnected. Auction halted.' });

                if (currentGameState.currentAuctionItem && currentGameState.currentAuctionItem.status === 'auctioning') {
                    const itemInList = currentGameState.items.find(item => item.id === currentGameState.currentAuctionItem.id);
                    if (itemInList) itemInList.status = 'pending';
                }
                if (currentGameState.currentHighestBidder && currentGameState.players[currentGameState.currentHighestBidder] && currentGameState.auctionState === 'bidding') {
                     const player = currentGameState.players[currentGameState.currentHighestBidder];
                    if (player.ws && player.ws.readyState === WebSocket.OPEN) {
                        player.budget += currentGameState.currentHighestBid;
                        sendToClient(player.ws, {
                            type: 'player_bid_update',
                            playerBudget: player.budget,
                            message: `Auctioneer disconnected. Your bid for "${currentGameState.currentAuctionItem.name}" refunded.`,
                            success: true
                        });
                    }
                }
                currentGameState.currentAuctionItem = null;
                currentGameState.currentHighestBid = 0;
                currentGameState.currentHighestBidder = null;
                currentGameState.auctionState = 'idle';
                stopAuctionTimer(ws.roomId);
                broadcastToRoom(ws.roomId, { type: 'auction_cleared', auctionState: currentGameState });

                // Check if all clients (including auctioneer's *active* socket) have disconnected
                const activePlayersInRoom = Object.values(currentGameState.players).filter(p => p.ws && p.ws.readyState === WebSocket.OPEN && p.playerId !== room.auctioneerPlayerId);
                const auctioneerActiveWs = room.activeAuctioneerWsId && Array.from(wss.clients).find(client => client.id === room.activeAuctioneerWsId && client.readyState === WebSocket.OPEN);

                if (activePlayersInRoom.length === 0 && !auctioneerActiveWs) {
                    stopAuctionTimer(ws.roomId);
                    delete gameRooms[ws.roomId];
                    console.log(`Room ${ws.roomId} deleted as all clients disconnected.`);
                }

            } else if (ws.role === 'player' && ws.playerId && currentGameState.players[ws.playerId]) {
                const playerName = currentGameState.players[ws.playerId].name;
                if (currentGameState.currentHighestBidder === ws.playerId) {
                    currentGameState.currentHighestBidder = null;
                    if (currentGameState.currentAuctionItem) {
                        currentGameState.currentHighestBid = currentGameState.currentAuctionItem.basePrice;
                    } else {
                        currentGameState.currentHighestBid = 0;
                    }
                    broadcastToRoom(ws.roomId, { type: 'info', message: `${playerName} disconnected. Highest bid retracted. Current bid reset to $${currentGameState.currentHighestBid.toLocaleString()}.` });
                    resetAuctionTimer(ws.roomId);
                    broadcastToRoom(ws.roomId, { type: 'auction_state_update', state: currentGameState });
                }
                currentGameState.players[ws.playerId].ws = null; // Mark player's websocket as null/invalidated

                broadcastToRoom(ws.roomId, { type: 'info', message: `${playerName} left the game.` });

                // Check if all clients have disconnected
                const activePlayersInRoom = Object.values(currentGameState.players).filter(p => p.ws && p.ws.readyState === WebSocket.OPEN && p.playerId !== room.auctioneerPlayerId);
                const auctioneerActiveWs = room.activeAuctioneerWsId && Array.from(wss.clients).find(client => client.id === room.activeAuctioneerWsId && client.readyState === WebSocket.OPEN);

                if (activePlayersInRoom.length === 0 && !auctioneerActiveWs) {
                    stopAuctionTimer(ws.roomId);
                    delete gameRooms[ws.roomId];
                    console.log(`Room ${ws.roomId} deleted as all clients disconnected.`);
                }
            }
        });

        ws.on('error', error => {
            console.error(`WebSocket Error for client ${ws.id} (Room: ${ws.roomId}, Role: ${ws.role}, PlayerId: ${ws.playerId}):`, error);
        });
    });

    server.listen(PORT, () => {
        console.log(`Server listening on http://localhost:${PORT}`);
        console.log(`WebSocket server running on ws://localhost:${PORT}`);
        if (!GEMINI_API_KEY) {
             console.warn("WARNING: GEMINI_API_KEY is not set. AI assistant will not function.");
        }
    });
})();