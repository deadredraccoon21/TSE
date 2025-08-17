// Keep this variable definition at the top
const userRole = USER_ROLE; 
console.log("User role:", userRole);

// =================================================================
// ===   CORE APPLICATION LOGIC (UNCHANGED)                      ===
// =================================================================

function closeTemplate() {
    document.body.classList.remove('blur-active');
    const mainContent = document.getElementById('main-content');
    mainContent.innerHTML = '';
    if (socket && socket.off) {
        socket.off('update');
    }
    fetch('/close_template', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' }
    }).catch(error => console.error('Error notifying server about template closure:', error));
    console.log('Template closed successfully');
}

function loadTemplate(parentSubmodule, settingOption) {
    console.log(parentSubmodule, settingOption);
    socket.on('update', function (data) {
        Object.keys(data).forEach((key) => {
            const element = document.getElementById(key);
            if (element) {
                if (element.classList.contains('value')) {
                    element.innerText = data[key];
                } else if (element.tagName === 'INPUT' && element.type === 'number') {
                    if (document.activeElement !== element) {
                        element.value = data[key];
                    }
                } else if (element.tagName === 'INPUT' && element.type === 'checkbox') {
                    element.checked = data[key];
                    const textElement = document.getElementById(element.id + '-text');
                    if (textElement) {
                        textElement.textContent = data[key] ? 'Manual' : 'Auto';
                    }
                }
            }
        });
    });

    fetch(`/load_template/${parentSubmodule}/${settingOption}`)
    .then(response => response.text())
    .then(data => {
        const mainContent = document.getElementById('main-content');
        mainContent.innerHTML = data;
        document.body.classList.add('blur-active');
        bindDynamicEvents();
    })
    .catch(error => console.error('Error loading template:', error));
}

function bindDynamicEvents() {
    const successButton = document.querySelector('.btn-success');
    if (successButton) {
        successButton.replaceWith(successButton.cloneNode(true));
        document.querySelector('.btn-success').addEventListener('click', updateSettings);
    }
    const dangerButton = document.querySelector('.btn-danger'); // Assuming a close button might have this class
    if (dangerButton) {
        dangerButton.addEventListener('click', closeDialog);
    }
    const modeInputs = document.querySelectorAll('input[type="checkbox"]');
    modeInputs.forEach(input => {
        input.addEventListener('change', function () {
            updateModeText(this, this.id + '-text', this.dataset.nodeid);
        });
    });
    // This function seems to be missing in the original code, but I'm leaving the call here.
    // If you have a `validateInput` function, it should be included.
    // const inputs = document.querySelectorAll('input[type="number"]');
    // inputs.forEach(input => {
    //     input.addEventListener('input', validateInput);
    // });
}

function updateSettings(event) {
    event.preventDefault();
    const setpoints = {};
    let isValid = true;
    document.querySelectorAll("input[data-nodeid]").forEach(input => {
        if (!isValid) return; // Stop processing if an error was found
        const nodeid = input.getAttribute("data-nodeid");
        const datatype = input.getAttribute("data-datatype");
        let value = input.value;
        const row = input.closest('.row-container');
        const parameter = row?.querySelector('.parameter')?.innerText || 'Unknown parameter';
        const parameters = row?.querySelector('.parameters')?.innerText || 'Unknown parameters';

        if (parameter.includes("Integration Time")) {
            if (value < 0 || value > 1000) {
                showErrorMessage(`❌ Invalid input for ${parameter}! Please enter a value between 0 and 1000.`);
                isValid = false;
            }
        } else if (parameters.includes("M VALUE")) {
            if (value < 0 || value > 1) {
                showErrorMessage(`❌ Invalid input for ${parameters}! Please enter a value of 0 or 1.`);
                isValid = false;
            }
        } else {
            if (value < 0 || value > 100) {
                showErrorMessage(`❌ Invalid input for ${parameter}! Please enter a value between 0 and 100.`);
                isValid = false;
            }
        }
        if (nodeid && isValid) {
            if (datatype === "boolean") {
                if (value !== "0" && value !== "1") {
                    showErrorMessage(`❌ Invalid boolean input for ${parameter}! Please enter either 0 or 1.`);
                    isValid = false;
                    return;
                }
                value = value === "1";
            } else {
                value = parseFloat(value);
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
        } else {
            showErrorMessage(`❌ Failed to update settings: ${data.error}`);
        }
    })
    .catch(error => console.error('Error updating settings:', error));
}

function showErrorMessage(message) {
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
    const mainContent = document.getElementById('main-content');
    mainContent.innerHTML = '';
    document.body.classList.remove('blur-active');
}

function updateModeText(toggle, textId, nodeId) {
    var isManual = toggle.checked;
    var text = isManual ? 'Manual' : 'Auto';
    document.getElementById(textId).textContent = text;
    var payload = {};
    payload[nodeId] = isManual;
    fetch('/writes', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
    }).catch(error => {
        console.error("Error in fetch:", error);
        alert("Network error: " + error.message);
    });
}

function makeDraggable(element) {
    let offsetX = 0, offsetY = 0, isDragging = false;
    element.addEventListener('mousedown', function (e) {
        isDragging = true;
        offsetX = e.clientX - element.offsetLeft;
        offsetY = e.clientY - element.offsetTop;
        document.body.style.cursor = 'move';
    });
    document.addEventListener('mousemove', function (e) {
        if (isDragging) {
            element.style.left = `${e.clientX - offsetX}px`;
            element.style.top = `${e.clientY - offsetY}px`;
        }
    });
    document.addEventListener('mouseup', function () {
        isDragging = false;
        document.body.style.cursor = 'default';
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
// ===   NEW UNIFIED DROPDOWN AND NAVIGATION LOGIC (REPLACES OLD CODE) ===
// =================================================================
$(document).ready(function() {

    // Initialize draggable functionality for dynamically loaded content
    const mainContent = document.getElementById('main-content');
    if(mainContent) {
        makeDraggable(mainContent);
    }

    // Initialize alarm count check
    fetchAlarmCount();
    setInterval(fetchAlarmCount, 2000);

    // // --- Global Click Handler to close popups/dropdowns ---
    // $(document).on('click', function(event) {
    //     // Close profile dropdown if click is outside of it
    //     if (!$(event.target).closest('.dropdown').length) {
    //         $('.dropdown-content').hide();
    //     }
    //     // Close sidebar dropdowns if click is outside the sidebar
    //     if (!$(event.target).closest('.sidebar').length) {
    //         $('.submodule-container').slideUp();
    //     }
    // });

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