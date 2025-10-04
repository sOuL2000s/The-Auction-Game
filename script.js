document.addEventListener('DOMContentLoaded', () => {
    // --- WebSocket Setup ---
    const PROTOCOL = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const HOST = window.location.host;
    const socket = new WebSocket(`${PROTOCOL}//${HOST}`);

    // --- UI Elements ---
    const messageArea = document.getElementById('message-area');
    const bidSound = document.getElementById('bidSound');
    const hammerSound = document.getElementById('hammerSound');
    const auctionHammerEffect = document.getElementById('auctionHammerEffect');

    // Room Selection Elements
    const roomSelectionArea = document.getElementById('room-selection-area');
    const roomIdInput = document.getElementById('roomIdInput');
    const joinRoomBtn = document.getElementById('joinRoomBtn');
    const createRoomBtn = document.getElementById('createRoomBtn');

    // Main Game Panels
    const playerPanel = document.getElementById('playerPanel');
    const mainAuctionArea = document.getElementById('mainAuctionArea');
    const auctioneerPanel = document.getElementById('auctioneerPanel');
    const scoreboardPanel = document.getElementById('scoreboardPanel');

    // Player Panel Tabs
    const playerTabsNav = playerPanel.querySelector('.tabs-nav');
    const playerTabButtons = playerPanel.querySelectorAll('.tabs-nav .tab-button');
    const playerTabContents = playerPanel.querySelectorAll('.tab-content');

    // Auctioneer Panel Tabs
    const auctioneerTabsNav = auctioneerPanel.querySelector('.tabs-nav');
    const auctioneerTabButtons = auctioneerPanel.querySelectorAll('.tabs-nav .tab-button');
    const auctioneerTabContents = auctioneerPanel.querySelectorAll('.tab-content');

    // Auctioneer Elements
    const auctioneerRoomIdDisplay = document.getElementById('auctioneerRoomIdDisplay');
    const itemNameInput = document.getElementById('itemNameInput');
    const itemBasePriceInput = document.getElementById('itemBasePriceInput');
    const addItemBtn = document.getElementById('addItemBtn');
    const auctioneerCurrentItemDiv = document.getElementById('auctioneer-current-item');
    const startBiddingBtn = document.getElementById('startBiddingBtn');
    const finalizeItemBtn = document.getElementById('finalizeItemBtn');
    const clearAuctionBtn = document.getElementById('clearAuctionBtn');
    const auctioneerItemsList = document.getElementById('auctioneerItemsList');
    // Auctioneer Batch Items
    const batchItemsInput = document.getElementById('batchItemsInput');
    const batchCsvFileInput = document.getElementById('batchCsvFileInput');
    const addBatchItemsBtn = document.getElementById('addBatchItemsBtn');

    // Auctioneer Settings
    const startingBudgetInput = document.getElementById('startingBudgetInput');
    const bidIncrementPercentInput = document.getElementById('bidIncrementPercentInput');
    const auctionDurationInput = document.getElementById('auctionDurationInput');
    const updateSettingsBtn = document.getElementById('updateSettingsBtn');

    // Player Elements (now within player-profile-winnings tab)
    const playerRoomIdDisplay = document.getElementById('playerRoomIdDisplay');
    const playerIdDisplay = document.getElementById('playerIdDisplay');
    const playerNameInput = document.getElementById('playerNameInput');
    const setPlayerNameBtn = document.getElementById('setPlayerNameBtn');
    const playerBudgetDisplay = document.getElementById('playerBudgetDisplay');

    // Player Current Auction elements (within player-auction-tab)
    const currentAuctionItemName = document.getElementById('currentAuctionItemName');
    const currentAuctionBid = document.getElementById('currentAuctionBid');
    const currentHighestBidder = document.getElementById('currentHighestBidder');
    const bidInputArea = document.getElementById('bid-input-area');
    const bidAmountInput = document.getElementById('bidAmountInput');
    const placeBidBtn = document.getElementById('placeBidBtn');

    // Player Winnings list (within player-profile-winnings tab)
    const playerWonItemsList = document.getElementById('playerWonItemsList');

    // Central Auction Display
    const centralAuctionItemDiv = document.getElementById('central-auction-item');
    const centralStatusMessage = document.getElementById('centralStatusMessage');
    const auctionTimerDisplay = document.getElementById('auction-timer-display');
    const timerCountdown = document.getElementById('timer-countdown');

    // Scoreboard Elements
    const playerScoresContainer = document.getElementById('playerScoresContainer');

    // Floating LLM Assistant Elements
    const globalFloatingLlmAssistant = document.getElementById('globalFloatingLlmAssistant');
    const toggleLlmChatBtn = document.getElementById('toggleLlmChatBtn');
    const globalLlmChat = document.getElementById('globalLlmChat');
    const globalLlmMessages = document.getElementById('globalLlmMessages');
    const globalLlmInput = document.getElementById('globalLlmInput');
    const globalLlmSendBtn = document.getElementById('globalLlmSendBtn');


    // --- Game State Variables (Client-side) ---
    let myClientId = null;
    let myPlayerId = null;
    let myRole = 'guest'; // 'guest', 'player', 'auctioneer'
    let currentRoomId = null;
    let myPlayerBudget = 0;
    let currentAuctionState = {};


    // --- Helper Functions ---
    function showMessage(message, type = 'info') {
        messageArea.textContent = message;
        messageArea.className = `message-area show ${type}`;
        setTimeout(() => {
            messageArea.classList.remove('show');
        }, 5000);
    }

    function playSound(audioElement) {
        if (audioElement && audioElement.readyState >= 2) {
            audioElement.currentTime = 0;
            audioElement.play().catch(e => console.warn("Audio play failed:", e));
        }
    }

    function addLlmMessage(chatWindow, message, sender = 'bot') {
        const p = document.createElement('p');
        p.classList.add('llm-message', sender);
        p.textContent = message;
        chatWindow.appendChild(p);
        chatWindow.scrollTop = chatWindow.scrollHeight;
    }

    // Generic tab activation function
    function activateTab(tabButtonsList, tabContentsList, tabId) {
        tabButtonsList.forEach(button => {
            if (button.dataset.tab === tabId) {
                button.classList.add('active');
            } else {
                button.classList.remove('active');
            }
        });
        tabContentsList.forEach(content => {
            if (content.id === tabId) {
                content.classList.add('active');
            } else {
                content.classList.remove('active');
            }
        });
    }

    function resetUI() {
        roomSelectionArea.style.display = 'flex';
        playerPanel.style.display = 'none';
        mainAuctionArea.style.display = 'none';
        auctioneerPanel.style.display = 'none';
        scoreboardPanel.style.display = 'none';
        globalFloatingLlmAssistant.style.display = 'none'; // Hide floating AI

        myClientId = null;
        myPlayerId = null;
        myRole = 'guest';
        currentRoomId = null;
        myPlayerBudget = 0;
        currentAuctionState = {};

        roomIdInput.value = '';
        playerRoomIdDisplay.textContent = '';
        auctioneerRoomIdDisplay.textContent = '';
        playerIdDisplay.textContent = '';
        playerNameInput.value = '';
        playerBudgetDisplay.textContent = '0';
        currentAuctionItemName.textContent = 'Awaiting Auction';
        currentAuctionBid.textContent = '0';
        currentHighestBidder.textContent = 'N/A';
        bidInputArea.style.display = 'none';
        bidAmountInput.value = '';
        playerWonItemsList.innerHTML = '<li class="placeholder-item">No items won yet.</li>';
        auctioneerItemsList.innerHTML = '<li class="placeholder-item">No items added yet.</li>';
        centralAuctionItemDiv.innerHTML = '<p class="status-message" id="centralStatusMessage">Auction awaits the Auctioneer.</p>';
        auctionTimerDisplay.style.display = 'none';
        timerCountdown.textContent = '00:00';
        playerScoresContainer.innerHTML = '<p class="placeholder-item">No players yet. Join a room!</p>';

        globalLlmMessages.innerHTML = '<p class="llm-message bot">Hello! Ask me anything about the auction rules or items.</p>';
        globalLlmChat.style.display = 'none'; // Collapse floating AI chat on reset

        joinRoomBtn.disabled = false;
        createRoomBtn.disabled = false;
    }


    // --- WebSocket Event Handlers ---
    socket.onopen = () => {
        console.log('Connected to WebSocket server');
        showMessage('Connected to The Grand Auction House server.', 'success');
        resetUI();

        // Use sessionStorage for per-tab session data
        const storedRoomId = sessionStorage.getItem('roomId');
        const storedPlayerId = sessionStorage.getItem('playerId');
        const storedPlayerName = sessionStorage.getItem('playerName');
        const storedRole = sessionStorage.getItem('role');

        if (storedRoomId && storedPlayerId && storedRole) {
            console.log(`Attempting to rejoin room ${storedRoomId} as ${storedRole} with ID ${storedPlayerId}`);
            socket.send(JSON.stringify({
                type: 'reconnect_session',
                roomId: storedRoomId,
                playerId: storedPlayerId,
                playerName: storedPlayerName,
                role: storedRole
            }));
        } else {
            console.log("No stored session found for this tab, showing room selection.");
            roomSelectionArea.style.display = 'flex';
        }
    };

    socket.onmessage = (event) => {
        const message = JSON.parse(event.data);
        console.log('Received:', message);

        switch (message.type) {
            case 'client_id_assigned':
                myClientId = message.id;
                console.log("My client ID is:", myClientId);
                break;

            case 'room_created':
                currentRoomId = message.roomId;
                myRole = 'auctioneer';
                myPlayerId = message.playerId; // Server now sends auctioneer's player ID on creation
                sessionStorage.setItem('roomId', currentRoomId);
                sessionStorage.setItem('playerId', myPlayerId);
                sessionStorage.setItem('role', myRole);
                showMessage(`Room "${currentRoomId}" created! You are the Auctioneer.`, 'success');
                displayGameUI();
                activateTab(auctioneerTabButtons, auctioneerTabContents, 'auction-control'); // Default tab for auctioneer
                globalFloatingLlmAssistant.style.display = 'flex'; // Show floating AI
                break;

            case 'joined_room':
                currentRoomId = message.roomId;
                myRole = message.role;
                myPlayerId = message.playerId;
                myPlayerBudget = message.budget;
                sessionStorage.setItem('roomId', currentRoomId);
                sessionStorage.setItem('playerId', myPlayerId);
                sessionStorage.setItem('playerName', message.playerName);
                sessionStorage.setItem('role', myRole);

                if (myRole === 'player') {
                    playerIdDisplay.textContent = myPlayerId.substring(0, 8);
                    playerNameInput.value = message.playerName || `Player ${myPlayerId.substring(0, 8)}`;
                    playerBudgetDisplay.textContent = myPlayerBudget.toLocaleString();
                    activateTab(playerTabButtons, playerTabContents, 'player-auction-tab'); // Default player tab
                }
                showMessage(`Joined room "${currentRoomId}" as ${myRole === 'auctioneer' ? 'Auctioneer' : 'Player'}!`, 'success');
                displayGameUI();
                if (myRole === 'auctioneer') activateTab(auctioneerTabButtons, auctioneerTabContents, 'auction-control');
                globalFloatingLlmAssistant.style.display = 'flex'; // Show floating AI
                break;

            case 'reconnected_session':
                currentRoomId = message.roomId;
                myRole = message.role;
                myPlayerId = message.playerId;
                myPlayerBudget = message.budget;
                sessionStorage.setItem('roomId', currentRoomId);
                sessionStorage.setItem('playerId', myPlayerId);
                sessionStorage.setItem('playerName', message.playerName);
                sessionStorage.setItem('role', myRole);

                if (myRole === 'player') {
                    playerIdDisplay.textContent = myPlayerId.substring(0, 8);
                    playerNameInput.value = message.playerName || `Player ${myPlayerId.substring(0, 8)}`;
                    playerBudgetDisplay.textContent = myPlayerBudget.toLocaleString();
                    activateTab(playerTabButtons, playerTabContents, 'player-auction-tab'); // Default player tab
                }
                showMessage(`Reconnected to room "${currentRoomId}" as ${message.playerName || myRole}!`, 'info');
                displayGameUI();
                if (myRole === 'auctioneer') activateTab(auctioneerTabButtons, auctioneerTabContents, 'auction-control');
                globalFloatingLlmAssistant.style.display = 'flex'; // Show floating AI
                break;

            case 'room_full':
            case 'room_not_found':
            case 'already_in_room':
            case 'auctioneer_exists':
                showMessage(message.message, 'error');
                roomSelectionArea.style.display = 'flex';
                globalFloatingLlmAssistant.style.display = 'none'; // Hide floating AI if re-joining fails
                break;

            case 'item_added':
                if (auctioneerItemsList.querySelector('.placeholder-item')) auctioneerItemsList.innerHTML = '';
                showMessage(`"${message.item.name}" added to the inventory.`, 'info');
                break;
            case 'batch_items_added':
                if (auctioneerItemsList.querySelector('.placeholder-item')) auctioneerItemsList.innerHTML = '';
                showMessage(`${message.count} items added to the inventory.`, 'success');
                break;

            case 'auction_state_update':
                currentAuctionState = message.state;
                updateAuctionDisplay(message.state);
                updateCentralAuctionDisplay(message.state);
                updateAuctioneerItems(message.state.items);
                updateTimerDisplay(message.state.timer);
                updateScoreboardDisplay(message.state);
                // Update player's budget and won items if they are in the current room
                if (myPlayerId && message.state.players[myPlayerId]) {
                    myPlayerBudget = message.state.players[myPlayerId].budget;
                    playerBudgetDisplay.textContent = myPlayerBudget.toLocaleString();
                    playerNameInput.value = message.state.players[myPlayerId].name; // Update player name in input
                    if (message.state.players[myPlayerId].wonItems.length > 0) {
                        playerWonItemsList.innerHTML = message.state.players[myPlayerId].wonItems.map(item =>
                            `<li><span class="item-name">${item.name}</span> <span class="item-price">($${item.finalBid.toLocaleString()})</span></li>`
                        ).join('');
                    } else {
                         playerWonItemsList.innerHTML = '<li class="placeholder-item">No items won yet.</li>';
                    }
                }
                break;

            case 'player_bid_update':
                currentAuctionState = message.auctionState;
                updateAuctionDisplay(message.auctionState, true);
                updateCentralAuctionDisplay(message.auctionState, true);
                if (myPlayerId && message.playerId === myPlayerId) {
                    myPlayerBudget = message.playerBudget;
                    playerBudgetDisplay.textContent = myPlayerBudget.toLocaleString();
                    bidAmountInput.value = ''; // Clear user's bid input after a successful bid
                }
                if (message.message) {
                    showMessage(message.message, message.success ? 'success' : 'error');
                }
                playSound(bidSound);
                updateTimerDisplay(message.auctionState.timer);
                updateScoreboardDisplay(message.auctionState);
                break;

            case 'item_finalized':
                currentAuctionState = message.auctionState;
                updateAuctionDisplay(message.auctionState);
                updateCentralAuctionDisplay(message.auctionState);
                updateAuctioneerItems(message.auctionState.items);
                updateTimerDisplay(message.auctionState.timer);
                if (myPlayerId && message.winnerId === myPlayerId) {
                    if (playerWonItemsList.querySelector('.placeholder-item')) playerWonItemsList.innerHTML = '';
                    playerWonItemsList.innerHTML += `<li><span class="item-name">${message.item.name}</span> <span class="item-price">($${message.finalBid.toLocaleString()})</span></li>`;
                }
                showMessage(`${message.item.name} SOLD! Winner: ${message.winnerName || message.winnerId.substring(0,8)} for $${message.finalBid.toLocaleString()}!`, 'success');
                playSound(hammerSound);
                triggerHammerEffect();
                updateScoreboardDisplay(message.auctionState);
                break;
            case 'auction_cleared':
                currentAuctionState = message.auctionState;
                updateAuctionDisplay(message.auctionState);
                updateCentralAuctionDisplay(message.auctionState);
                updateAuctioneerItems(message.auctionState.items);
                updateTimerDisplay(message.auctionState.timer);
                showMessage('Auction cleared by the Auctioneer.', 'info');
                updateScoreboardDisplay(message.auctionState);
                break;
            case 'settings_updated':
                currentAuctionState.gameSettings = message.settings;
                showMessage('Game settings updated by Auctioneer.', 'info');
                if(myRole === 'auctioneer') {
                    startingBudgetInput.value = currentAuctionState.gameSettings.playerStartingBudget;
                    bidIncrementPercentInput.value = currentAuctionState.gameSettings.minBidIncrementPercentage;
                    auctionDurationInput.value = currentAuctionState.gameSettings.auctionRoundDuration;
                }
                updateScoreboardDisplay(currentAuctionState);
                break;

            case 'llm_response':
                // LLM responses always target the global chat window
                addLlmMessage(globalLlmMessages, message.response, 'bot');
                break;

            case 'error':
                showMessage(message.message, 'error');
                break;
            case 'info':
                showMessage(message.message, 'info');
                break;
        }
    };

    socket.onclose = () => {
        console.log('Disconnected from WebSocket server');
        showMessage('Disconnected from auction server. Please restart the server or check connection.', 'error');

        // Clear sessionStorage on disconnect to force new session on reload
        sessionStorage.removeItem('roomId');
        sessionStorage.removeItem('playerId');
        sessionStorage.removeItem('playerName');
        sessionStorage.removeItem('role');

        [joinRoomBtn, createRoomBtn, setPlayerNameBtn, addItemBtn, startBiddingBtn, finalizeItemBtn, clearAuctionBtn, placeBidBtn, updateSettingsBtn, addBatchItemsBtn, globalLlmSendBtn].forEach(btn => btn.disabled = true);
        auctionTimerDisplay.style.display = 'none';

        resetUI();
    };

    socket.onerror = (error) => {
        console.error('WebSocket Error:', error);
        showMessage('WebSocket connection error. Is the server running?', 'error');
    };


    // --- UI Display Management ---
    function displayGameUI() {
        roomSelectionArea.style.display = 'none';
        playerPanel.style.display = 'flex';
        mainAuctionArea.style.display = 'flex';
        scoreboardPanel.style.display = 'flex';
        globalFloatingLlmAssistant.style.display = 'flex'; // Ensure floating AI is shown once in a room

        if (myRole === 'auctioneer') {
            playerPanel.style.display = 'none';
            auctioneerPanel.style.display = 'flex';
            auctioneerRoomIdDisplay.textContent = currentRoomId;
            if (currentAuctionState.gameSettings) {
                startingBudgetInput.value = currentAuctionState.gameSettings.playerStartingBudget;
                bidIncrementPercentInput.value = currentAuctionState.gameSettings.minBidIncrementPercentage;
                auctionDurationInput.value = currentAuctionState.gameSettings.auctionRoundDuration;
            }
        } else {
            auctioneerPanel.style.display = 'none';
        }

        playerRoomIdDisplay.textContent = currentRoomId;
    }

    function updateAuctioneerItems(items) {
        auctioneerItemsList.innerHTML = '';
        if (items.length === 0) {
            auctioneerItemsList.innerHTML = '<li class="placeholder-item">No items added yet.</li>';
            return;
        }
        items.forEach(item => {
            const li = document.createElement('li');
            li.classList.add('item-row');
            if (item.status === 'auctioning') {
                li.classList.add('current-auction-item');
            } else if (item.status === 'sold') {
                li.classList.add('sold-item');
            }

            li.innerHTML = `
                <span class="item-details">${item.name} - Base: $${item.basePrice.toLocaleString()} </span>
                <span class="item-status">${item.status === 'pending' ? ' (Ready)' : item.status === 'auctioning' ? ' (LIVE!)' : ' (SOLD)'}</span>
                ${myRole === 'auctioneer' && item.status === 'pending' ? `<button class="select-item-btn action-btn">Select for Auction</button>` : ''}
            `;
            const selectBtn = li.querySelector('.select-item-btn');
            if (selectBtn) {
                selectBtn.dataset.itemId = item.id;
            }

            auctioneerItemsList.appendChild(li);
        });
    }

    function updateAuctionDisplay(state, isNewBid = false) {
        const itemName = state.currentAuctionItem ? state.currentAuctionItem.name : 'Awaiting Auction';
        const currentBid = state.currentHighestBid;
        const highestBidderName = state.currentHighestBidder ? (state.players[state.currentHighestBidder]?.name || state.currentHighestBidder.substring(0,8)) : 'N/A';

        currentAuctionItemName.textContent = itemName;
        currentAuctionBid.textContent = currentBid.toLocaleString();
        currentHighestBidder.textContent = highestBidderName;

        if (state.auctionState === 'bidding' && myPlayerId) {
            bidInputArea.style.display = 'block';
            placeBidBtn.disabled = false;
            const minBidValue = Math.ceil(currentBid * (1 + state.gameSettings.minBidIncrementPercentage / 100));
            bidAmountInput.min = minBidValue;

            if (bidAmountInput.value === '') {
                bidAmountInput.value = minBidValue;
            } else if (document.activeElement !== bidAmountInput && parseFloat(bidAmountInput.value) < minBidValue) {
                bidAmountInput.value = minBidValue;
            }
        } else {
            bidInputArea.style.display = 'none';
            bidAmountInput.value = '';
        }

        if (isNewBid) {
            currentAuctionBid.classList.add('new-bid-effect');
            setTimeout(() => {
                currentAuctionBid.classList.remove('new-bid-effect');
            }, 300);
        }

        if (myRole === 'auctioneer') {
            if (state.currentAuctionItem) {
                auctioneerCurrentItemDiv.innerHTML = `
                    <p class="current-auction-item">
                        <strong>Current Item: ${state.currentAuctionItem.name}</strong><br>
                        Base Price: $${state.currentAuctionItem.basePrice.toLocaleString()}<br>
                        Current Bid: $${state.currentHighestBid.toLocaleString()}<br>
                        Highest Bidder: ${highestBidderName}<br>
                        Status: ${state.auctionState === 'bidding' ? 'Bidding In Progress' : 'Item Selected, Awaiting Bid Start'}
                    </p>
                `;
                finalizeItemBtn.style.display = (state.auctionState === 'bidding' && state.currentHighestBidder) ? 'inline-block' : 'none';
                startBiddingBtn.style.display = (state.auctionState === 'item_selected' && state.currentAuctionItem) ? 'inline-block' : 'none';
                clearAuctionBtn.style.display = (state.auctionState !== 'idle') ? 'inline-block' : 'none';
            } else {
                auctioneerCurrentItemDiv.innerHTML = '<p>No item currently selected for auction.</p>';
                startBiddingBtn.style.display = 'none';
                finalizeItemBtn.style.display = 'none';
                clearAuctionBtn.style.display = 'none';
            }
        }
    }

    function updateCentralAuctionDisplay(state, isNewBid = false) {
        if (!state.currentAuctionItem) {
            centralAuctionItemDiv.innerHTML = `<p class="status-message" id="centralStatusMessage">Auction awaits the Auctioneer.</p>`;
            return;
        }

        const itemName = state.currentAuctionItem.name;
        const currentBid = state.currentHighestBid;
        const highestBidderName = state.currentHighestBidder ? (state.players[state.currentHighestBidder]?.name || state.currentHighestBidder.substring(0,8)) : 'N/A';

        centralAuctionItemDiv.innerHTML = `
            <h3>${itemName}</h3>
            <p>Base Price: $${state.currentAuctionItem.basePrice.toLocaleString()}</p>
            <p class="central-bid">Current Bid: $<span id="centralCurrentBidValue">${currentBid.toLocaleString()}</span></p>
            <p class="central-highest-bidder">Highest Bidder: ${highestBidderName}</p>
            <p class="status-message">${state.auctionState === 'bidding' ? 'Bidding Live!' : 'Item Selected'}</p>
        `;

        if (isNewBid) {
            const centralBidElement = centralAuctionItemDiv.querySelector('.central-bid');
            if (centralBidElement) {
                centralBidElement.classList.add('new-bid-effect');
                setTimeout(() => {
                    centralBidElement.classList.remove('new-bid-effect');
                }, 300);
            }
        }
    }

    function updateTimerDisplay(timerData) {
        if (timerData && timerData.active && currentAuctionState.auctionState === 'bidding') {
            auctionTimerDisplay.style.display = 'flex';
            const remaining = Math.max(0, Math.floor((timerData.endTime - Date.now()) / 1000));
            const minutes = Math.floor(remaining / 60).toString().padStart(2, '0');
            const seconds = (remaining % 60).toString().padStart(2, '0');
            timerCountdown.textContent = `${minutes}:${seconds}`;

            if (remaining <= 5 && remaining > 0) {
                auctionTimerDisplay.classList.add('time-low');
            } else {
                auctionTimerDisplay.classList.remove('time-low');
            }
        } else {
            auctionTimerDisplay.style.display = 'none';
            auctionTimerDisplay.classList.remove('time-low');
        }
    }

    function triggerHammerEffect() {
        auctionHammerEffect.classList.add('show');
        setTimeout(() => {
            auctionHammerEffect.classList.remove('show');
        }, 1500);
    }

    function updateScoreboardDisplay(state) {
        playerScoresContainer.innerHTML = '';
        const players = Object.values(state.players);

        if (players.length === 0) {
            playerScoresContainer.innerHTML = '<p class="placeholder-item">No players yet. Join a room!</p>';
            return;
        }

        players.sort((a, b) => {
            const aTotalValue = a.wonItems.reduce((sum, item) => sum + item.finalBid, 0);
            const bTotalValue = b.wonItems.reduce((sum, item) => sum + item.finalBid, 0);
            return bTotalValue - aTotalValue;
        });

        players.forEach(player => {
            const playerCard = document.createElement('div');
            playerCard.classList.add('player-score-card');
            if (player.playerId === myPlayerId) {
                playerCard.classList.add('current-player-score');
            }

            const totalWonValue = player.wonItems.reduce((sum, item) => sum + item.finalBid, 0);

            playerCard.innerHTML = `
                <h4>${player.name} <span class="player-budget-score">($${player.budget.toLocaleString()} remaining)</span></h4>
                <p>Total Won: $<span class="total-won-value">${totalWonValue.toLocaleString()}</span></p>
                <ul class="player-winnings-list">
                    ${player.wonItems.length > 0
                        ? player.wonItems.map(item => `<li><span class="item-name">${item.name}</span> <span class="item-price">($${item.finalBid.toLocaleString()})</span></li>`).join('')
                        : '<li class="placeholder-item-small">No items won yet.</li>'
                    }
                </ul>
            `;
            playerScoresContainer.appendChild(playerCard);
        });
    }


    // --- Event Listeners ---

    // Room Actions
    createRoomBtn.addEventListener('click', () => {
        socket.send(JSON.stringify({ type: 'create_room', clientId: myClientId }));
    });

    joinRoomBtn.addEventListener('click', () => {
        const roomId = roomIdInput.value.trim();
        if (roomId) {
            socket.send(JSON.stringify({ type: 'join_room', roomId: roomId, clientId: myClientId }));
        } else {
            showMessage('Please enter a Room ID.', 'error');
        }
    });

    // Player Actions
    playerTabsNav.addEventListener('click', (event) => {
        if (event.target.closest('.tab-button')) {
            const tabId = event.target.closest('.tab-button').dataset.tab;
            activateTab(playerTabButtons, playerTabContents, tabId);
        }
    });

    setPlayerNameBtn.addEventListener('click', () => {
        const name = playerNameInput.value.trim();
        if (name && myPlayerId && currentRoomId) {
            socket.send(JSON.stringify({ type: 'set_player_name', roomId: currentRoomId, playerId: myPlayerId, name }));
            sessionStorage.setItem('playerName', name); // Update sessionStorage
        } else {
            showMessage('Please enter a valid display name.', 'error');
        }
    });

    placeBidBtn.addEventListener('click', () => {
        const bidAmount = parseFloat(bidAmountInput.value);
        const minBid = parseFloat(bidAmountInput.min);

        if (!currentRoomId || !myPlayerId) {
            showMessage('You are not in a game room or not registered as a player.', 'error');
            return;
        }

        if (isNaN(bidAmount) || bidAmount < minBid) {
            showMessage(`Bid must be at least $${minBid.toLocaleString()}.`, 'error');
            return;
        }
        socket.send(JSON.stringify({ type: 'place_bid', roomId: currentRoomId, playerId: myPlayerId, bidAmount }));
    });

    // Auctioneer Actions
    auctioneerTabsNav.addEventListener('click', (event) => {
        if (event.target.closest('.tab-button')) {
            const tabId = event.target.closest('.tab-button').dataset.tab;
            activateTab(auctioneerTabButtons, auctioneerTabContents, tabId);
        }
    });

    updateSettingsBtn.addEventListener('click', () => {
        if (!currentRoomId || myRole !== 'auctioneer') {
            showMessage('Only the auctioneer in an active room can update settings.', 'error');
            return;
        }
        const playerStartingBudget = parseFloat(startingBudgetInput.value);
        const minBidIncrementPercentage = parseFloat(bidIncrementPercentInput.value);
        const auctionRoundDuration = parseInt(auctionDurationInput.value);

        if (isNaN(playerStartingBudget) || playerStartingBudget < 100 ||
            isNaN(minBidIncrementPercentage) || minBidIncrementPercentage < 1 || minBidIncrementPercentage > 100 ||
            isNaN(auctionRoundDuration) || auctionRoundDuration < 5 || auctionRoundDuration > 120) {
            showMessage('Please enter valid settings: Budget (min 100), Increment (1-100%), Duration (5-120s).', 'error');
            return;
        }

        socket.send(JSON.stringify({
            type: 'update_settings',
            roomId: currentRoomId,
            settings: {
                playerStartingBudget,
                minBidIncrementPercentage,
                auctionRoundDuration
            }
        }));
    });

    addItemBtn.addEventListener('click', () => {
        if (!currentRoomId || myRole !== 'auctioneer') {
            showMessage('Only the auctioneer in an active room can add items.', 'error');
            return;
        }
        const name = itemNameInput.value.trim();
        const basePrice = parseFloat(itemBasePriceInput.value);
        if (name && !isNaN(basePrice) && basePrice >= 0) {
            socket.send(JSON.stringify({ type: 'add_item', roomId: currentRoomId, name, basePrice }));
            itemNameInput.value = '';
            itemBasePriceInput.value = '';
        } else {
            showMessage('Please enter a valid item name and base price.', 'error');
        }
    });

    auctioneerItemsList.addEventListener('click', (event) => {
        if (!currentRoomId || myRole !== 'auctioneer') return;
        if (event.target.classList.contains('select-item-btn')) {
            const itemId = event.target.dataset.itemId;
            socket.send(JSON.stringify({ type: 'select_item_for_auction', roomId: currentRoomId, itemId }));
        }
    });

    startBiddingBtn.addEventListener('click', () => {
        if (!currentRoomId || myRole !== 'auctioneer') return;
        socket.send(JSON.stringify({ type: 'start_bidding', roomId: currentRoomId }));
    });

    finalizeItemBtn.addEventListener('click', () => {
        if (!currentRoomId || myRole !== 'auctioneer') return;
        socket.send(JSON.stringify({ type: 'finalize_item', roomId: currentRoomId }));
    });

    clearAuctionBtn.addEventListener('click', () => {
        if (!currentRoomId || myRole !== 'auctioneer') return;
        socket.send(JSON.stringify({ type: 'clear_auction', roomId: currentRoomId }));
    });

    batchItemsInput.addEventListener('keydown', (e) => {
        if (e.key === 'Tab') {
            e.preventDefault();
            const start = batchItemsInput.selectionStart;
            const end = batchItemsInput.selectionEnd;
            batchItemsInput.value = batchItemsInput.value.substring(0, start) + '\t' + batchItemsInput.value.substring(end);
            batchItemsInput.selectionStart = batchItemsInput.selectionEnd = start + 1;
        }
    });

    addBatchItemsBtn.addEventListener('click', () => {
        if (!currentRoomId || myRole !== 'auctioneer') {
            showMessage('Only the auctioneer in an active room can add items.', 'error');
            return;
        }

        let rawData = batchItemsInput.value.trim();
        const file = batchCsvFileInput.files[0];

        if (file) {
            const reader = new FileReader();
            reader.onload = (e) => {
                rawData = e.target.result;
                sendBatchItems(rawData);
            };
            reader.onerror = () => {
                showMessage('Error reading file.', 'error');
            };
            reader.readAsText(file);
        } else if (rawData) {
            sendBatchItems(rawData);
        } else {
            showMessage('No valid items found in the input. Format: "Name,Price" per line.', 'error');
        }
    });

    function sendBatchItems(data) {
        const items = data.split('\n').map(line => {
            const parts = line.split(',').map(p => p.trim());
            if (parts.length >= 2) {
                const name = parts[0];
                const basePrice = parseFloat(parts[1]);
                if (name && !isNaN(basePrice) && basePrice >= 0) {
                    return { name, basePrice };
                }
            }
            return null;
        }).filter(item => item !== null);

        if (items.length > 0) {
            socket.send(JSON.stringify({ type: 'add_batch_items', roomId: currentRoomId, items }));
            batchItemsInput.value = '';
            batchCsvFileInput.value = '';
        } else {
            showMessage('No valid items found in the input. Format: "Name,Price" per line.', 'error');
        }
    }


    // Floating LLM Assistant Listeners
    toggleLlmChatBtn.addEventListener('click', () => {
        if (globalLlmChat.style.display === 'none' || globalLlmChat.style.display === '') {
            globalLlmChat.style.display = 'flex';
            toggleLlmChatBtn.classList.add('active');
            globalLlmMessages.scrollTop = globalLlmMessages.scrollHeight; // Scroll to bottom on open
        } else {
            globalLlmChat.style.display = 'none';
            toggleLlmChatBtn.classList.remove('active');
        }
    });

    globalLlmSendBtn.addEventListener('click', () => {
        const query = globalLlmInput.value.trim();
        if (query && currentRoomId && myRole !== 'guest') { // Only allow if in a room and has a role
            addLlmMessage(globalLlmMessages, query, 'user');
            socket.send(JSON.stringify({ type: 'llm_query', roomId: currentRoomId, query, clientId: myClientId, role: myRole }));
            globalLlmInput.value = '';
        } else if (query && myRole === 'guest') {
            addLlmMessage(globalLlmMessages, query, 'user');
            addLlmMessage(globalLlmMessages, "Please join or create a room to use the AI assistant for game-specific queries.", 'bot');
            globalLlmInput.value = '';
        } else if (!query) {
             showMessage("Please type something to ask the AI.", "error");
        }
    });

    globalLlmInput.addEventListener('keypress', (e) => {
        if (e.key === 'Enter') globalLlmSendBtn.click();
    });


    // Initial population for placeholder lists
    if (playerWonItemsList.innerHTML === '') {
        playerWonItemsList.innerHTML = '<li class="placeholder-item">No items won yet.</li>';
    }
    if (auctioneerItemsList.innerHTML === '') {
        auctioneerItemsList.innerHTML = '<li class="placeholder-item">No items added yet.</li>';
    }
    if (playerScoresContainer.innerHTML === '') {
        playerScoresContainer.innerHTML = '<p class="placeholder-item">No players yet. Join a room!</p>';
    }
});