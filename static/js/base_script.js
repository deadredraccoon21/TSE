// Keep this variable definition at the top
const userRole = USER_ROLE; 
console.log("User role:", userRole);

// =================================================================
// ===   CORE APPLICATION LOGIC (REVISED AND UNIFIED)            ===
// =================================================================

function closeTemplate() {
    document.body.classList.remove('blur-active');
    const mainContent = document.getElementById('main-content');
    mainContent.innerHTML = '';
    if (window.socket && socket.off) {
        socket.off('update');
    }
    fetch('/close_template', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' }
    }).catch(error => console.error('Error notifying server about template closure:', error));
    console.log('Template closed successfully');
}

function loadTemplate(parentSubmodule, settingOption) {
    console.log(`Loading template: ${parentSubmodule}/${settingOption}`);
    
    // Ensure any previous socket listener is removed to prevent duplicates
    if (window.socket && socket.off) {
        socket.off('update');
    }

    // =========================================================================
    // ===   START: MODIFIED SOCKET LISTENER                                 ===
    // =========================================================================
    socket.on('update', function (data) {
        Object.keys(data).forEach((key) => {
            // FIX: Use querySelectorAll to find ALL elements with this ID, not just the first one.
            // This is crucial for updating both the main page and the dialog simultaneously.
            const elements = document.querySelectorAll(`[id="${key}"]`); 
            
            if (elements.length === 0) return;

            // Loop through each found element (e.g., the span on the main page AND the div in the dialog)
            elements.forEach(element => {
                // UNIVERSAL RULE: Do not update an element the user is actively editing.
                if (element.classList.contains('user-modified') || document.activeElement === element) {
                    return; 
                }

                // Update logic for different element types
                const value = data[key];

                if (element.classList.contains('value')) {
                    if (typeof value === 'number') {
                        element.innerText = value.toFixed(1);
                    } else {
                        element.innerText = value ? '1' : '0'; // Handles booleans
                    }
                } else if (element.tagName === 'INPUT' && element.type === 'number') {
                    if (typeof value === 'number') {
                        element.value = value.toFixed(1);
                    } else {
                        element.value = value ? '1' : '0';
                    }
                } else if (element.tagName === 'INPUT' && element.type === 'checkbox') {
                    element.checked = value;
                    const textElement = document.getElementById(element.id + '-text');
                    if (textElement) {
                        textElement.textContent = value ? 'Manual' : 'Auto';
                    }
                } 
                // NEW: Added handling for SPAN tags like those on the spinning2.html page
                else if (element.tagName === 'SPAN') {
                    if (typeof value === 'number') {
                        element.textContent = value.toFixed(1);
                    } else {
                        // Handles booleans, strings, and null/undefined gracefully
                        element.textContent = (value !== null && value !== undefined) ? value : 'NA';
                    }
                }
            });
        });
    });
    // =========================================================================
    // ===   END: MODIFIED SOCKET LISTENER                                   ===
    // =========================================================================

    fetch(`/load_template/${parentSubmodule}/${settingOption}`)
    .then(response => response.text())
    .then(data => {
        const mainContent = document.getElementById('main-content');
        mainContent.innerHTML = data;
        document.body.classList.add('blur-active');
        bindDynamicEvents(); // Bind events to the newly loaded content
        const dialogBox = mainContent.querySelector('.dialog-box');
        if (dialogBox) {
            makeDraggable(dialogBox);
        }
    })
    .catch(error => console.error('Error loading template:', error));
}

function bindDynamicEvents() {
    // Bind the Update button
    const successButton = document.querySelector('.btn-success');
    if (successButton) {
        successButton.addEventListener('click', updateSettings);
    }

    // Bind the Close button
    const dangerButton = document.querySelector('.btn-danger');
    if (dangerButton) {
        dangerButton.addEventListener('click', closeDialog);
    }

    // Bind all mode switches (sliders)
    const modeInputs = document.querySelectorAll('input[type="checkbox"][data-nodeid]');
    modeInputs.forEach(input => {
        input.addEventListener('change', () => updateMode(input));
    });

    // Bind all editable number inputs to mark them when changed by the user
    const numberInputs = document.querySelectorAll('input.setpoint-input');
    numberInputs.forEach(input => {
        input.addEventListener('input', () => {
            input.classList.add('user-modified');
        });
    });
}

function updateSettings(event) {
    event.preventDefault();
    const setpoints = {};
    let isValid = true;
    // Only select the inputs that the user has actually changed
    const inputsToUpdate = document.querySelectorAll("input.setpoint-input.user-modified[data-nodeid]");

    if (inputsToUpdate.length === 0) {
        // Optional: show a message if nothing was changed
        // showErrorMessage("No changes to update.");
        return;
    }

    inputsToUpdate.forEach(input => {
        if (!isValid) return; 

        const nodeid = input.getAttribute("data-nodeid");
        const datatype = input.getAttribute("data-datatype");
        let value = input.value;
        const row = input.closest('.row-container');
        // Find the label for this row to use in error messages
        const parameterLabel = row?.querySelector('.parameter')?.innerText || 'Parameter';

        // Use specific validation from your original function
        if (parameterLabel.includes("Integration Time")) {
            if (value < 0 || value > 1000) {
                showErrorMessage(`❌ Invalid Integration Time! Please enter a value between 0 and 1000.`);
                isValid = false;
            }
        }
        // General validation for other parameters can be added here if needed

        if (nodeid && isValid) {
            if (datatype === "boolean") {
                if (value !== "0" && value !== "1") {
                    showErrorMessage(`❌ Invalid boolean input for ${parameterLabel}! Must be 0 or 1.`);
                    isValid = false;
                    return;
                }
                value = (value === "1");
            } else {
                value = parseFloat(value);
                if (isNaN(value)) {
                    showErrorMessage(`❌ Invalid number for ${parameterLabel}.`);
                    isValid = false;
                    return;
                }
            }
            setpoints[nodeid] = value;
        }
    });

    if (!isValid) return;

    fetch('/write', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(setpoints),
    })
    .then(response => response.json())
    .then(data => {
        if (data.success) {
            // Show the custom success pop-up
            if (!document.getElementById('update-popup')) {
                const popup = document.createElement('div');
                popup.id = 'update-popup';
                popup.innerText = '✅ Updated Successfully!';
                Object.assign(popup.style, {
                    position: 'fixed', top: '13%', left: '55%', transform: 'translate(-50%, -50%)',
                    background: '#005fa3', color: 'white', padding: '8px 28px', borderRadius: '4px',
                    boxShadow: '0 4px 8px rgba(0, 0, 0, 0.2)', fontSize: '12px', fontWeight: 'bold',
                    textAlign: 'center', zIndex: '1000', opacity: '0', transition: 'opacity 0.5s ease-in-out'
                });
                document.body.appendChild(popup);
                setTimeout(() => { popup.style.opacity = '1'; }, 10);
                setTimeout(() => {
                    popup.style.opacity = '0';
                    setTimeout(() => popup.remove(), 500);
                }, 2000);
            }
            // IMPORTANT: Un-mark the inputs after a successful save
            inputsToUpdate.forEach(input => input.classList.remove('user-modified'));
        } else {
            showErrorMessage(`❌ Failed to update settings: ${data.error || 'Unknown Error'}`);
        }
    })
    .catch(error => {
        console.error('Error updating settings:', error);
        showErrorMessage(`❌ Network Error: Could not update settings.`);
    });
}

function showErrorMessage(message) {
    // Check if an error popup already exists to avoid stacking them
    if (document.getElementById('error-popup')) return;

    const errorPopup = document.createElement('div');
    errorPopup.id = 'error-popup';
    errorPopup.innerText = message;
    Object.assign(errorPopup.style, {
        position: 'fixed', top: '13%', left: '55%', transform: 'translate(-50%, -50%)',
        background: '#ff4d4d', color: 'white', padding: '8px 28px', borderRadius: '4px',
        boxShadow: '0 4px 8px rgba(0, 0, 0, 0.2)', fontSize: '12px', fontWeight: 'bold',
        textAlign: 'center', zIndex: '1000', opacity: '0', transition: 'opacity 0.5s ease-in-out'
    });
    document.body.appendChild(errorPopup);
    setTimeout(() => { errorPopup.style.opacity = '1'; }, 10);
    setTimeout(() => {
        errorPopup.style.opacity = '0';
        setTimeout(() => errorPopup.remove(), 500);
    }, 3000);
}

function closeDialog() {
    // When closing, remove any "user-modified" tags to discard changes
    document.querySelectorAll('.user-modified').forEach(el => el.classList.remove('user-modified'));
    
    const mainContent = document.getElementById('main-content');
    mainContent.innerHTML = '';
    document.body.classList.remove('blur-active');
}

/**
 * Handles immediate updates for mode switches (sliders).
 */
function updateMode(checkbox) {
    const nodeId = checkbox.dataset.nodeid;
    const isManual = checkbox.checked;

    if (!nodeId) {
        console.error("No nodeid found for switch:", checkbox.id);
        return;
    }

    // Optimistically update the UI text
    const textElement = document.getElementById(checkbox.id + '-text');
    if (textElement) {
        textElement.textContent = isManual ? 'Manual' : 'Auto';
    }

    const payload = { [nodeId]: isManual };
    fetch('/write', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    }).catch(error => {
        console.error("Error in fetch:", error);
        // Revert UI on failure
        checkbox.checked = !isManual;
        if (textElement) {
            textElement.textContent = isManual ? 'Auto' : 'Manual';
        }
        showErrorMessage("Network error: Could not update mode.");
    });
}

function makeDraggable(element) {
    let offsetX = 0, offsetY = 0, isDragging = false;
    // Only make draggable by the header
    const header = element.querySelector('.header');
    if (!header) return;

    header.style.cursor = 'move';
    header.addEventListener('mousedown', function (e) {
        // Prevent dragging if the click is on a button inside the header
        if (e.target.tagName === 'BUTTON') return;
        
        isDragging = true;
        // Calculate offset relative to the main dialog box, not the header
        offsetX = e.clientX - element.offsetLeft;
        offsetY = e.clientY - element.offsetTop;
        document.body.style.userSelect = 'none'; // Prevent text selection while dragging
    });
    document.addEventListener('mousemove', function (e) {
        if (isDragging) {
            element.style.left = `${e.clientX - offsetX}px`;
            element.style.top = `${e.clientY - offsetY}px`;
        }
    });
    document.addEventListener('mouseup', function () {
        isDragging = false;
        document.body.style.userSelect = '';
    });
}

// =================================================================
// ===           ALARM & IFRAME LOGIC (UNCHANGED)                ===
// =================================================================

let alarmModalOpen = false;
let alarmRefreshInterval = null;

function refreshAlarmContent() {
    fetch("/notifyAlarms").then(response => response.json()).then(data => {
        let alarmMessage = "";
        if (data.length === 0) {
            alarmMessage = "<p>No active alarms.</p>";
        } else {
            alarmMessage = data.slice(0, 10).map(a => `<p><span class="alarm-time">${a.time}</span>${a.message} &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp;&nbsp; ${a.status}</p>`).join("");
        }
        document.getElementById("alarm-modal-body").innerHTML = alarmMessage;
    }).catch(error => {
        document.getElementById("alarm-modal-body").innerHTML = "<p>Error loading alarms.</p>";
        console.error("Error fetching alarms:", error);
    });
}

function showAlarmslist() {
    if (alarmModalOpen) return;
    alarmModalOpen = true;
    document.getElementById("alarmModal").style.display = "flex";
    document.body.classList.add("alarm-modal-active");
    refreshAlarmContent();
    if (!alarmRefreshInterval) {
        alarmRefreshInterval = setInterval(refreshAlarmContent, 1000);
    }
}

function closeModal() {
    alarmModalOpen = false;
    document.getElementById("alarmModal").style.display = "none";
    document.body.classList.remove("alarm-modal-active");
    if (alarmRefreshInterval) {
        clearInterval(alarmRefreshInterval);
        alarmRefreshInterval = null;
    }
}

function fetchAlarmCount() {
    fetch("/notifyAlarms").then(response => response.json()).then(data => {
        const unacknowledgedCount = data.filter(a => a.status === "Not acknowledged").length;
        const alarmCountElement = document.getElementById("alarm-count");
        if (unacknowledgedCount > 0) {
            alarmCountElement.style.display = "inline-block";
            alarmCountElement.innerText = unacknowledgedCount;
        } else {
            alarmCountElement.style.display = "none";
        }
    }).catch(error => console.error("Error fetching alarm count:", error));
}

function showIframe(iframeId) {
    document.querySelectorAll('iframe').forEach(iframe => iframe.style.display = 'none');
    document.getElementById(iframeId).style.display = 'block';
}

// =================================================================
// ===   NEW UNIFIED DROPDOWN AND NAVIGATION LOGIC (UNCHANGED)   ===
// =================================================================
$(document).ready(function() {

    // Initialize alarm count check
    fetchAlarmCount();
    setInterval(fetchAlarmCount, 2000);

    // --- Sidebar Navigation and Dropdown Logic ---
    // 1. Handle simple navigation buttons that go to a new page
    $('#home').on('click', function() { window.location.href = '/dashboard'; });
    $('#alarms').on('click', function() { window.location.href = '/alarmslist'; });
    $('#inputs').on('click', function() { window.location.href = '/input'; });
    // Handle user management links
    $('button[onclick="window.location.href=\'/add_user\'"]').on('click', function() { window.location.href = '/add_user'; });
    $('button[onclick="window.location.href=\'/user_management\'"]').on('click', function() { window.location.href = '/user_management'; });


    // 2. Handle module buttons that just toggle a dropdown menu
    $('.module-button').on('click', function(event) {
        // Only act on buttons that are NOT simple navigation links
        const id = $(this).attr('id');
        if (id && !['home', 'alarms', 'inputs'].includes(id)) {
            event.stopPropagation(); // Prevent document click from closing it right away
            var $dropdown = $(this).next('.submodule-container');
            // Close other open submodule dropdowns
            $('.submodule-container').not($dropdown).slideUp();
            // Toggle the current dropdown
            $dropdown.slideToggle();
        }
    });

    // 3. Handle the special "Departments" submodule links to prevent reloads
    $('.department-link').on('click', function(event) {
        event.preventDefault(); // Always prevent default button action
        event.stopPropagation(); // Stop the click from bubbling up

        const url = $(this).data('url');
        const submoduleId = $(this).data('submodule-id');
        const $icon = $(this).find('.dropdown-icon');
        const $submoduleContainer = $('#' + submoduleId);

        // Check if we are already on the target page by comparing paths
        const targetPath = new URL(url, window.location.origin).pathname;
        const isCurrentPage = window.location.pathname === targetPath;

        if (isCurrentPage) {
            // If already on the page, just toggle the settings dropdown
            const isVisible = $submoduleContainer.is(':visible');
            $submoduleContainer.slideToggle();
            $icon.toggleClass('fa-chevron-up', !isVisible).toggleClass('fa-chevron-down', isVisible);
        } else {
            // If on a different page, navigate, but first save which dropdown to open
            sessionStorage.setItem('openSubmoduleOnLoad', submoduleId);
            window.location.href = url;
        }
    });

    // On page load, check if we need to open a department's settings dropdown
    const submoduleToOpen = sessionStorage.getItem('openSubmoduleOnLoad');
    if (submoduleToOpen) {
        const $container = $('#' + submoduleToOpen);
        if ($container.length) {
            $container.show();
            $('#departmentsSubmodules').show(); // Ensure parent is also open
            const $button = $(`.department-link[data-submodule-id="${submoduleToOpen}"]`);
            if ($button.length) {
                $button.find('.dropdown-icon').removeClass('fa-chevron-down').addClass('fa-chevron-up');
            }
        }
        sessionStorage.removeItem('openSubmoduleOnLoad'); // Clean up
    }

    // --- Profile Dropdown Logic ---
    $('.profile-icon').on('click', function(event) {
        event.stopPropagation();
        $('.dropdown-content').toggle();
    });

    // --- Tooltip Logic ---
    $('.profile-icon').hover(
        function() { $('#profile-tooltip').show(); },
        function() { $('#profile-tooltip').hide(); }
    );
    
    // Prevent dropdowns from closing when clicking inside them
    $('.submodule-container, .dropdown').on('click', function(event) {
        event.stopPropagation();
    });
});