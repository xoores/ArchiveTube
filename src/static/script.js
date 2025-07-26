const config_modal = document.getElementById("config-modal");
const save_changes_button = document.getElementById("save-changes-button");
const sync_status_button = document.getElementById("sync-status-button");
const sync_start_times = document.getElementById("sync-start-times");
const ignore_ssl_errors = document.getElementById("ignore-ssl-errors");
const yt_slow = document.getElementById("yt-slow");
const media_server_addresses = document.getElementById("media-server-addresses");
const media_server_tokens = document.getElementById("media-server-tokens");
const media_server_library_name = document.getElementById("media-server-library-name");
const sync_status_button_icon = document.getElementById("sync-status-button-icon");
const add_channel = document.getElementById("add-channel");
const total_library_size = document.getElementById("total-library-size");
const channel_table = document.getElementById("channel-table").querySelector("tbody");
const modal_channel_template = document.getElementById("modal-channel-template").content;
let channel_list = [];
const socket = io();

function number_si_suffix( bytes ) {
    if (!+bytes) return '0 B'

    const k = 1000
    const dm = 1
    const sizes = ['B', 'kB', 'MB', 'GB', 'TB', 'PB', 'EB', 'ZB', 'YB']

    const i = Math.floor(Math.log(bytes) / Math.log(k))

    return `${parseFloat((bytes / Math.pow(k, i)).toFixed(dm))} ${sizes[i]}`
}

function change_filter_description(negate_filter_checkbox, filter_text_description) {
    filter_text_description.textContent = negate_filter_checkbox.checked
        ? "Ignore videos with this text in the title."
        : "Only get videos with this text in the title.";
}

function open_edit_modal(channel_id) {
    const channel = channel_list.find(c => c.Id === channel_id);

    if (!channel) {
        alert("Error, Channel not found");
        return;
    }
    const channel_edit_modal_container = document.createElement("div");
    channel_edit_modal_container.appendChild(document.importNode(modal_channel_template, true));

    const modal = channel_edit_modal_container.querySelector("#modal-channel-config");
    const negate_filter_checkbox = modal.querySelector("#negate-filter");
    const filter_text_description = modal.querySelector("#filter-text-description");

    modal.querySelector("#channel-name").value = channel.Name;
    modal.querySelector("#channel-link").value = channel.Link;
    modal.querySelector("#download-days").value = channel.DL_Days;
    modal.querySelector("#keep-days").value = channel.Keep_Days;
    modal.querySelector("#title-filter-text").value = channel.Filter_Title_Text;
    negate_filter_checkbox.checked = channel.Negate_Filter;
    modal.querySelector("#search-limit").value = channel.Search_Limit;
    modal.querySelector("#set-remove-sponsored").checked = channel.Use_SponsorBlock;
    modal.querySelector("#set-best-quality").checked = channel.Use_Best_Quality;
    modal.querySelector("#set-audio-only").checked = channel.Audio_Only;
    modal.querySelector("#set-write-info-json").checked = channel.Write_Info_Json;
    modal.querySelector("#set-mtime").checked = channel.Set_Mtime;

    change_filter_description(negate_filter_checkbox, filter_text_description);

    negate_filter_checkbox.addEventListener("change", () => {
        change_filter_description(negate_filter_checkbox, filter_text_description);
    });

    modal.querySelectorAll("input[name='live-rule-selector']").forEach(radio => {
        if (radio.value === channel.Live_Rule) {
            radio.checked = true;
        }
    });

    document.body.appendChild(channel_edit_modal_container);
    const modal_edit_channel = new bootstrap.Modal(modal);
    modal_edit_channel.show();

    modal.querySelector("#save-channel-changes-button").addEventListener("click", function () {
        save_channel_changes(channel);
    });

    modal.addEventListener("hidden.bs.modal", function () {
        channel_edit_modal_container.remove();
    });
}

function add_row_to_channel_table(channel) {
    const template = document.getElementById("channel-row-template");
    const new_row = document.importNode(template.content, true);
    const row = new_row.querySelector("tr");

    row.id = channel.Id;
    row.querySelector(".channel-name").innerHTML = "<a class=\"text-decoration-none\" target=\"_blank\" href=\"" + channel.Link + "\">" + channel.Name + "</a>";
    row.querySelector(".channel-last-synced").textContent = channel.Last_Synced;
    row.querySelector(".channel-item-count").textContent = channel.Item_Count + " / " + channel.Remote_Count;
    row.querySelector(".channel-item-size").textContent = number_si_suffix(channel.Item_Size);

    const edit_button = row.querySelector(".edit-button");
    edit_button.addEventListener("click", function () {
        open_edit_modal(channel.Id);
    });

    const remove_button = row.querySelector(".remove-button");
    remove_button.addEventListener("click", function () {
        remove_channel(channel);
    });

    const pause_button = row.querySelector(".pause-button");
    const pause_button_icon = row.querySelector(".pause-btn-icon");

    if( channel.Paused ) {
        pause_button_icon.classList.remove("fa-play")
        pause_button_icon.classList.add("fa-pause")
        pause_button_icon.classList.add("fa-fade")
        pause_button.title = "Channel synchronization paused, click to resume"
    } else {
        pause_button_icon.classList.add("fa-play")
        pause_button_icon.classList.remove("fa-pause")
        pause_button_icon.classList.remove("fa-fade")
        pause_button.title = "Channel synchronization enabled, click to pause"

    }
    //pause_button_icon.classList = [ "fa-solid", "fa-pause" ]
    //pause_button.textContent = channel.Paused ? "Resume" : "Pause";


    pause_button.addEventListener("click", function () {
        pause_channel(channel, pause_button);
    });

    channel_table.appendChild(row);
}

function remove_channel(channel_to_be_removed) {
    const confirmation = confirm(`Are you sure you want to remove the channel "${channel_to_be_removed.Name}"?`);
    if (confirmation) {
        socket.emit("remove_channel", channel_to_be_removed);

        const index = channel_list.findIndex(c => c.Id === channel_to_be_removed.Id);
        if (index > -1) {
            channel_list.splice(index, 1);
            const row = document.getElementById(`${channel_to_be_removed.Id}`);
            if (row) {
                row.remove();
            }
        }
    }
}
function pause_channel(channel_to_be_paused, pause_button) {
    if( !channel_to_be_paused.Paused ) {
        const confirmation = confirm(`Do you want to pause channel "${channel_to_be_paused.Name}"?`);
        if (!confirmation) return;
    }

    channel_to_be_paused.Paused = !channel_to_be_paused.Paused
    socket.emit("pause_channel", channel_to_be_paused);
}

function save_channel_changes(channel) {
    let download_days = parseInt(document.getElementById("download-days").value, 10);
    let keep_days = parseInt(document.getElementById("keep-days").value, 10);

    if (keep_days !== -1 && keep_days < download_days) {
        keep_days = download_days;
        document.getElementById("keep-days").value = keep_days;
    }

    const channel_updates = {
        Id: channel.Id,
        Name: document.getElementById("channel-name").value,
        Link: document.getElementById("channel-link").value,
        DL_Days: parseInt(document.getElementById("download-days").value, 10),
        Keep_Days: parseInt(document.getElementById("keep-days").value, 10),
        Filter_Title_Text: document.getElementById("title-filter-text").value,
        Negate_Filter: document.getElementById("negate-filter").checked,
        Search_Limit: parseInt(document.getElementById("search-limit").value, 10),
        Audio_Only: document.getElementById("set-audio-only").checked,
        Use_SponsorBlock: document.getElementById("set-remove-sponsored").checked,
        Use_Best_Quality: document.getElementById("set-best-quality").checked,
        Write_Info_Json: document.getElementById("set-write-info-json").checked,
        Set_Mtime: document.getElementById("set-mtime").checked,
    };

    socket.emit("save_channel_changes", channel_updates);
    const index = channel_list.findIndex(c => c.Id === channel.Id);
    if (index > -1) {
        channel_list[index] = channel_updates;
        const row = document.getElementById(channel.Id);
        if (row) {
            row.querySelector(".channel-name").textContent = channel_updates.Name;
        }
    }
}

add_channel.addEventListener("click", function () {
    socket.emit("add_channel");
});

config_modal.addEventListener("show.bs.modal", function (event) {
    socket.emit("get_settings");
});

sync_status_button.addEventListener("click", () => {
    socket.emit("sync_toggle");
});

save_changes_button.addEventListener("click", () => {
    socket.emit("save_settings", {
        "sync_start_times": sync_start_times.value,
        "media_server_addresses": media_server_addresses.value,
        "media_server_tokens": media_server_tokens.value,
        "media_server_library_name": media_server_library_name.value,
        "ignore_ssl_errors": ignore_ssl_errors.checked,
        "youtube_slow": yt_slow.checked,
    });
});

socket.on("settings_save_message", function (message) {
    const save_settings_message = document.getElementById("save-settings-message");
    if (save_settings_message) {
        save_settings_message.style.display = "block";
        save_settings_message.textContent = message;
        setTimeout(() => {
            save_settings_message.style.display = "none";
        }, 1000);
    }
});

socket.on("channel_save_message", function (message) {
    const save_channel_message = document.getElementById("save-channel-message");
    if (save_channel_message) {
        save_channel_message.style.display = "block";
        save_channel_message.textContent = message;
        setTimeout(() => {
            save_channel_message.style.display = "none";
        }, 1000);
    }
});

socket.on("update_channel_list", function (data) {
    console.log(data)

    channel_table.innerHTML = "";
    channel_list = data.Channel_List;
    let total_size = 0;
    console.log(data);
    for( const channel of channel_list ) {
        let channel_size = parseInt(channel.Item_Size);
        if( !isNaN(channel_size) ) total_size += channel_size;

        add_row_to_channel_table(channel);
    }

    total_library_size.textContent = number_si_suffix(total_size);
});

socket.on("new_channel_added", function (new_channel) {
    channel_list.push(new_channel);
    add_row_to_channel_table(new_channel);
});

socket.on("sync_state_changed", function (sync_state) {
    console.log("sync_state_changed", sync_state);
    switch( sync_state.Sync_State ) {
        case "run":
            sync_status_button_icon.classList.remove("fa-circle-stop", "fa-circle-check", "fa-circle-exclamation")
            sync_status_button_icon.classList.add("fa-circle-down", "fa-beat-fade")
            break;

        case "stop":
            sync_status_button_icon.classList.remove("fa-circle-down", "fa-beat-fade")
            if( sync_state.Success ) {
                sync_status_button_icon.classList.add("fa-circle-check")
            } else {
                sync_status_button_icon.classList.add("fa-circle-exclamation")
            }
            break;
    }

});

socket.on("current_settings", function (settings) {
    sync_start_times.value = settings.sync_start_times.join(", ");
    media_server_addresses.value = settings.media_server_addresses;
    media_server_tokens.value = settings.media_server_tokens;
    media_server_library_name.value = settings.media_server_library_name;
    ignore_ssl_errors.checked = settings.ignore_ssl_errors;
    yt_slow.checked = settings.youtube_slow;
});


const themeSwitch = document.getElementById('themeSwitch');
const savedTheme = localStorage.getItem('theme');
const savedSwitchPosition = localStorage.getItem('switchPosition');

if (savedSwitchPosition) {
    themeSwitch.checked = savedSwitchPosition === 'true';
}

if (savedTheme) {
    document.documentElement.setAttribute('data-bs-theme', savedTheme);
}

themeSwitch.addEventListener('click', () => {
    if (document.documentElement.getAttribute('data-bs-theme') === 'dark') {
        document.documentElement.setAttribute('data-bs-theme', 'light');
    } else {
        document.documentElement.setAttribute('data-bs-theme', 'dark');
    }
    localStorage.setItem('theme', document.documentElement.getAttribute('data-bs-theme'));
    localStorage.setItem('switchPosition', themeSwitch.checked);
});
