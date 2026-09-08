// ApexCharts uses "MM" for months; the logger date format uses "mm".
function toApexDateFormat(format) {
    return format.replace(/mm/g, 'MM');
}

var chartDateFormat = toApexDateFormat(dateFormat);

// https://apexcharts.com/javascript-chart-demos/line-charts/zoomable-timeseries/
var options = {
    series: [],
    chart: {
        type: 'area',
        stacked: false,
        height: 490,
        zoom: {
            type: 'x',
            enabled: true,
            autoScaleYaxis: true
        },
        // background: '#2B2D3E',
        foreColor: '#fff'
    },
    dataLabels: {
        enabled: false
    },
    stroke: {
        curve: 'smooth',
    },
    markers: {
        size: 0,
    },
    title: {
        text: 'Channel points (dates are displayed in UTC)',
        align: 'left'
    },
    colors: ["#f9826c"],
    fill: {
        type: 'gradient',
        gradient: {
            shadeIntensity: 1,
            inverseColors: false,
            opacityFrom: 0.5,
            opacityTo: 0,
            stops: [0, 90, 100]
        },
    },
    yaxis: {
        title: {
            text: 'Channel points'
        },
    },
    xaxis: {
        type: 'datetime',
        labels: {
            datetimeUTC: false,
            format: chartDateFormat
        }
    },
    tooltip: {
        theme: 'dark',
        shared: false,
        x: {
            show: true,
            format: `${chartDateFormat} HH:mm:ss`,
        },
        custom: ({
            series,
            seriesIndex,
            dataPointIndex,
            w
        }) => {
            return (`<div class="apexcharts-active">
                <div class="apexcharts-tooltip-title">${w.globals.seriesNames[seriesIndex]}</div>
                <div class="apexcharts-tooltip-series-group apexcharts-active" style="order: 1; display: flex; padding-bottom: 0px !important;">
                    <div class="apexcharts-tooltip-text">
                        <div class="apexcharts-tooltip-y-group">
                            <span class="apexcharts-tooltip-text-label"><b>Points</b>: ${series[seriesIndex][dataPointIndex]}</span><br>
                            <span class="apexcharts-tooltip-text-label"><b>Reason</b>: ${w.globals.seriesZ[seriesIndex][dataPointIndex] ? w.globals.seriesZ[seriesIndex][dataPointIndex] : ''}</span>
                        </div>
                    </div>
                </div>
                </div>`)
        }
    },
    noData: {
        text: 'Loading...'
    }
};

var chart = new ApexCharts(document.querySelector("#chart"), options);
var chartRendered = false;
var currentStreamer = null;
var pointSeries = [];
var annotations = [];
var streamerRefreshTimeout = null;
var streamerDataRequest = 0;

var streamersList = [];
var sortBy = "Name ascending";
var sortField = 'name';
var dropsRefreshTimeout = null;
var dropsState = { categories: {}, orderedCategories: [] };
var currentDropCategory = null;
var dropsFilter = 'active';
var dropsPage = 1;
var dropsPerPage = 10;
var streamerDeleteSelectionMode = false;
var selectedStreamerAnalytics = new Set();
var lastStreamerCheckboxIndex = null;
var pendingDeleteStreamers = [];
var analyticsDeleteInProgress = false;
var pointsLoaded = false;
var dropsLoaded = false;
var configLoaded = false;
var logsLoaded = false;
var webConfigState = null;

// Variable to keep track of whether auto-update log is active
var autoUpdateLog = true;

// Variable to keep track of the last received log index
var lastReceivedLogIndex = 0;
var initialLogTailBytes = 128 * 1024;
// The markup's initial #log-content text is only a placeholder shown before
// the first real response arrives - cleared on that first response instead
// of staying glued above the real tail forever.
var logPlaceholderCleared = false;

// Load a recent tail first, then request only entries appended after it.
// Lazily started the first time the Logs tab is opened, then keeps polling
// in the background (like the Drops/Now Watching refreshes) until paused.
function getLog() {
    $.get(`/log?lastIndex=${lastReceivedLogIndex}&tailBytes=${initialLogTailBytes}`).done(function (data, _status, xhr) {
        if (!logPlaceholderCleared) {
            $("#log-content").empty();
            logPlaceholderCleared = true;
        }
        // Process and display the new log entries received
        // Logs contain Twitch-controlled text (for example prediction
        // titles), so never interpret them as HTML.
        $("#log-content").append(document.createTextNode(data));
        // Scroll to the bottom of the log content
        $("#log-content").scrollTop($("#log-content")[0].scrollHeight);

        // Update the last received log index
        const nextPosition = Number(xhr.getResponseHeader("X-Log-Position"));
        if (Number.isSafeInteger(nextPosition) && nextPosition >= 0) {
            lastReceivedLogIndex = nextPosition;
        }
    }).always(function () {
        // A rollover can briefly replace the active log between polls.
        // Retry transient failures without advancing the byte position.
        if (autoUpdateLog) {
            setTimeout(getLog, logPollInterval);
        }
    });
}

function startLogPolling() {
    if (logsLoaded) return;
    logsLoaded = true;
    getLog();
}

function showAnalyticsLoadError(message, details) {
    console.error(`[analytics] ${message}`, details || '');
    $('#analytics-load-error').text(message).show();
}

function clearAnalyticsLoadError() {
    if (pointsLoaded && dropsLoaded) {
        $('#analytics-load-error').text('').hide();
    }
}

function switchDashboardTab(tabName) {
    var isPoints = tabName === 'points';
    var isDrops = tabName === 'drops';
    var isConfig = tabName === 'config';
    var isLogs = tabName === 'logs';
    $('#points-panel').toggle(isPoints);
    $('#drops-panel').toggle(isDrops);
    $('#config-panel').toggle(isConfig);
    $('#logs-panel').toggle(isLogs);

    $('#tab-points').toggleClass('is-link', isPoints);
    $('#tab-drops').toggleClass('is-link', isDrops);
    $('#tab-config').toggleClass('is-link', isConfig);
    $('#tab-logs').toggleClass('is-link', isLogs);

    safeStorage.setItem('dashboardTab', tabName);

    if (isConfig && !configLoaded) loadWebConfig();
    if (isLogs && !logsLoaded) startLogPolling();

    // ApexCharts cannot reliably place annotations while its panel is hidden.
    // Reapply them after Points becomes visible, including when the page was
    // refreshed with the Drops tab saved in localStorage.
    if (isPoints && chartRendered) {
        window.requestAnimationFrame(function () {
            renderPointsChart();
            window.dispatchEvent(new Event('resize'));
        });
    }
}

var startDate = new Date();
startDate.setDate(startDate.getDate() - daysAgo);
var endDate = new Date();

$(document).ready(function () {
    var savedDarkMode = safeStorage.getItem('dark-mode');
    if (savedDarkMode === null) {
        savedDarkMode = 'true';
        safeStorage.setItem('dark-mode', savedDarkMode);
    }
    $('#dark-mode').prop('checked', savedDarkMode === 'true');
    $('#dark-theme').prop('disabled', savedDarkMode !== 'true');

    var savedDashboardTab = safeStorage.getItem('dashboardTab') || 'points';
    // A caller embedding this page (e.g. the Windows desktop shell's first-run
    // Config view) can force the initial tab via a URL hash, bypassing
    // whatever was last saved for this browser profile.
    var requestedTab = (window.location.hash || '').replace('#', '');
    if (['points', 'drops', 'config', 'logs'].includes(requestedTab)) {
        savedDashboardTab = requestedTab;
    }
    dropsFilter = safeStorage.getItem('dropsFilter') || 'active';
    $('#drops-filter').val(dropsFilter);

    $('#auto-update-log').click(() => {
        autoUpdateLog = !autoUpdateLog;
        $('#auto-update-log').text(autoUpdateLog ? '⏸️' : '▶️');

        if (autoUpdateLog) {
            getLog();
        }
    });

    // Retrieve the saved header visibility preference from localStorage
    var headerVisibility = safeStorage.getItem('headerVisibility');

    // Set the initial header visibility based on the saved preference or default to 'visible'
    if (headerVisibility === 'hidden') {
        $('#toggle-header').prop('checked', false);
        $('#header').hide();
        $('body').addClass('header-hidden');
    } else {
        $('#toggle-header').prop('checked', true);
        $('#header').show();
        $('body').removeClass('header-hidden');
    }

    // Handle the toggle header change event
    $('#toggle-header').change(function () {
        if (this.checked) {
            $('#header').show();
            $('body').removeClass('header-hidden');
            // Save the header visibility preference as 'visible' in localStorage
            safeStorage.setItem('headerVisibility', 'visible');
        } else {
            $('#header').hide();
            $('body').addClass('header-hidden');
            // Save the header visibility preference as 'hidden' in localStorage
            safeStorage.setItem('headerVisibility', 'hidden');
        }
    });

    // ApexCharts must initialize while its container is visible. If the saved
    // tab is Drops, hide Points only after chart initialization completes.
    chart.render().then(function () {
        chartRendered = true;
        switchDashboardTab(savedDashboardTab);
    });

    if (!safeStorage.getItem("annotations")) safeStorage.setItem("annotations", true);
    if (!safeStorage.getItem("sort-by")) safeStorage.setItem("sort-by", "Name ascending");

    // Restore settings from localStorage on page load
    $('#annotations').prop("checked", safeStorage.getItem("annotations") === "true");

    // Handle the annotation toggle click event
    $('#annotations').click(() => {
        var isChecked = $('#annotations').prop("checked");
        safeStorage.setItem("annotations", isChecked);
        updateAnnotations();
    });

    // Handle the dark mode toggle click event
    $('#dark-mode').click(() => {
        var isChecked = $('#dark-mode').prop("checked");
        safeStorage.setItem("dark-mode", isChecked);
        toggleDarkMode();
    });

    $('#startDate').val(formatDate(startDate));
    $('#endDate').val(formatDate(endDate));

    $('#delete-streamer-analytics').click(toggleStreamerAnalyticsDeletion);
    $('#cancel-streamer-analytics-selection').click(function () {
        setStreamerDeleteSelectionMode(false);
    });
    $('#analytics-delete-modal-cancel').click(closeAnalyticsDeleteModal);
    $('#analytics-delete-modal-confirm').click(confirmStreamerAnalyticsDeletion);
    $('#analytics-delete-modal').click(function (event) {
        if (event.target === this) closeAnalyticsDeleteModal();
    });
    $('#confirm-modal-cancel').click(closeConfirmDialog);
    $('#confirm-modal-confirm').click(function () {
        var callback = confirmDialogCallback;
        closeConfirmDialog();
        if (callback) callback();
    });
    $('#confirm-modal').click(function (event) {
        if (event.target === this) closeConfirmDialog();
    });
    $(document).on('keydown', function (event) {
        if (event.key === 'Escape' && $('#confirm-modal').hasClass('is-active')) closeConfirmDialog();
    });
    $('#add-streamer-form').submit(function (event) {
        event.preventDefault();
        addWebConfigValue('streamers', $('#new-streamer'));
    });
    $('#add-category-form').submit(function (event) {
        event.preventDefault();
        addWebConfigValue('categories', $('#new-category'));
    });
    $('#category-settings-form').submit(saveCategorySettings);
    $('#source-settings-form').submit(saveSourceSettings);
    $('#logging-settings-form').submit(saveLoggingSettings);
    $('#update-settings-form').submit(saveUpdateSettings);
    $('#update-check-startup-only').change(toggleUpdateInterval);
    $('#dismiss-update-banner').click(dismissUpdateBanner);
    $(document).keydown(function (event) {
        if (event.key === 'Escape') closeAnalyticsDeleteModal();
    });

    sortBy = safeStorage.getItem("sort-by");
    if (sortBy.includes("Points")) sortField = 'points';
    else if (sortBy.includes("Last activity")) sortField = 'last_activity';
    else sortField = 'name';
    $('#sorting-by').text(sortBy);
    getStreamers();
    getDropsByCategory();
    getNowWatching();

    updateAnnotations();
    toggleDarkMode();

});

function dismissUpdateBanner() {
    var banner = $('#update-available-banner');
    var version = banner.data('version');
    if (!version) return;
    document.cookie = `${updateDismissalCookie}=${encodeURIComponent(version)}; Max-Age=31536000; Path=/; SameSite=Lax`;
    banner.fadeOut(200);
}

function formatDate(date) {
    var d = new Date(date),
        month = '' + (d.getMonth() + 1),
        day = '' + d.getDate(),
        year = d.getFullYear();

    if (month.length < 2) month = '0' + month;
    if (day.length < 2) day = '0' + day;

    return [year, month, day].join('-');
}

function formatDisplayDate(date) {
    var d = new Date(date);
    if (isNaN(d.getTime())) return '';

    var values = {
        yyyy: String(d.getFullYear()),
        yy: String(d.getFullYear()).slice(-2),
        mm: String(d.getMonth() + 1).padStart(2, '0'),
        dd: String(d.getDate()).padStart(2, '0')
    };
    return dateFormat.replace(/yyyy|yy|mm|dd/g, function (token) {
        return values[token];
    });
}

function changeStreamer(streamer, index) {
    if (!streamer) {
        currentStreamer = null;
        pointSeries = [];
        annotations = [];
        safeStorage.removeItem("selectedStreamer");
        updateStreamerDeleteControls();
        options.title.text = 'Channel points (dates are displayed in UTC)';
        renderPointsChart();
        return;
    }

    $("li").removeClass("is-active")
    $("li").eq(index - 1).addClass('is-active');
    currentStreamer = streamer;
    updateStreamerDeleteControls();

    // Update the chart title with the current streamer's name
    options.title.text = `${streamer.replace(".json", "")}'s channel points (dates are displayed in UTC)`;
    if (chartRendered && !$('#points-panel').is(':hidden')) {
        chart.updateOptions({ title: options.title }, false, false);
    }

    // Save the selected streamer in localStorage
    safeStorage.setItem("selectedStreamer", currentStreamer);

    getStreamerData(streamer);
}

function getStreamerData(streamer) {
    if (streamerRefreshTimeout) {
        clearTimeout(streamerRefreshTimeout);
        streamerRefreshTimeout = null;
    }

    if (streamer && currentStreamer == streamer) {
        var request = ++streamerDataRequest;
        $.getJSON(`./json/${streamer}`, {
            startDate: formatDate(startDate),
            endDate: formatDate(endDate)
        }, function (response) {
            // Ignore a response for a range or streamer that has since changed.
            if (request !== streamerDataRequest || currentStreamer !== streamer) return;

            pointSeries = response["series"] || [];
            annotations = response["annotations"];
            renderPointsChart();
            streamerRefreshTimeout = setTimeout(function () {
                getStreamerData(streamer);
            }, 300000); // 5 minutes
        });
    }
}

function getAllStreamersData() {
    $.getJSON(`./json_all`, function (response) {
        for (var i in response) {
            chart.appendSeries({
                name: response[i]["name"].replace(".json", ""),
                data: response[i]["data"]["series"]
            }, true)
        }
    });
}

function getStreamers() {
    $.getJSON('streamers', function (response) {
        pointsLoaded = true;
        clearAnalyticsLoadError();
        console.debug('[analytics] Points response', response);
        streamersList = response;
        sortStreamers();
        var availableStreamers = new Set(streamersList.map(streamer => streamer.name));
        selectedStreamerAnalytics = new Set(
            Array.from(selectedStreamerAnalytics).filter(streamer => availableStreamers.has(streamer))
        );
        if (streamersList.length === 0) streamerDeleteSelectionMode = false;

        // Restore the selected streamer from localStorage on page load
        var selectedStreamer = safeStorage.getItem("selectedStreamer");

        if (selectedStreamer && streamersList.some(streamer => streamer.name === selectedStreamer)) {
            currentStreamer = selectedStreamer;
        } else {
            // If no selected streamer is found, default to the first streamer in the list
            currentStreamer = streamersList.length > 0 ? streamersList[0].name : null;
            if (currentStreamer) safeStorage.setItem("selectedStreamer", currentStreamer);
            else safeStorage.removeItem("selectedStreamer");
        }

        // Ensure the selected streamer is still active and scrolled into view
        renderStreamers();
    }).fail(function (xhr, status, error) {
        pointsLoaded = false;
        showAnalyticsLoadError(
            `Points failed to load (${xhr.status || status}): ${error || xhr.responseText || 'Unknown error'}`,
            xhr.responseText
        );
    });
}

function renderStreamers() {
    $("#streamers-list").empty();
    streamersList.forEach((streamer, index) => {
        var isActive = currentStreamer === streamer.name;
        if (!isActive && safeStorage.getItem("selectedStreamer") === null && index === 0) {
            isActive = true;
            currentStreamer = streamer.name;
        }

        var listItem = $('<li>').attr('id', `streamer-${index}`).toggleClass('is-active', isActive);
        var row = $('<div>').addClass('streamer-list-row');
        if (streamerDeleteSelectionMode) {
            var checkbox = $('<input>')
                .attr({
                    type: 'checkbox',
                    'aria-label': `Select ${streamer.name.replace(".json", "")} analytics data`,
                    'data-index': index
                })
                .addClass('streamer-delete-checkbox')
                .prop('checked', selectedStreamerAnalytics.has(streamer.name))
                .click(function (event) {
                    handleStreamerAnalyticsSelection(event, index, streamer.name);
                });
            row.append(checkbox);
        }

        var streamerLink = $('<a>').attr('href', '#').click(function (event) {
            event.preventDefault();
            changeStreamer(streamer.name, index + 1);
        });
        streamerLink.append(
            $('<span>').addClass('streamer-name').text(streamer.name.replace(".json", ""))
        );
        if (sortField == 'points') {
            streamerLink.append($('<span>').addClass('streamer-sort-value').text(streamer.points));
        } else if (sortField == 'last_activity') {
            streamerLink.append(
                $('<span>').addClass('streamer-sort-value').text(formatDisplayDate(streamer.last_activity))
            );
        }
        row.append(streamerLink);
        listItem.append(row);
        $("#streamers-list").append(listItem);
        if (isActive) {
            // Scroll the selected streamer into view
            listItem[0].scrollIntoView({
                behavior: 'smooth',
                block: 'center'
            });
        }
    });

    changeStreamer(currentStreamer, streamersList.findIndex(streamer => streamer.name === currentStreamer) + 1);
    updateStreamerDeleteControls();
}

function toggleStreamerAnalyticsDeletion() {
    if (!streamerDeleteSelectionMode) {
        if (streamersList.length > 0) setStreamerDeleteSelectionMode(true);
        return;
    }
    if (selectedStreamerAnalytics.size === 0) return;

    pendingDeleteStreamers = streamersList
        .filter(streamer => selectedStreamerAnalytics.has(streamer.name))
        .map(streamer => streamer.name);
    var displayNames = pendingDeleteStreamers.map(streamer => streamer.replace(".json", ""));
    var displayPreview = displayNames.slice(0, 5).join(', ');
    if (displayNames.length > 5) displayPreview += `, and ${displayNames.length - 5} more`;
    var streamerLabel = displayNames.length === 1
        ? displayNames[0]
        : `${displayNames.length} streamers (${displayPreview})`;
    $('#analytics-delete-modal').removeClass('is-error').addClass('is-active').attr('aria-hidden', 'false');
    $('#analytics-delete-modal .analytics-modal-icon i').attr('class', 'fas fa-trash-alt');
    $('#analytics-delete-modal-title').text('Delete analytics data?');
    $('#analytics-delete-modal-message').text(`Permanently delete all analytics history for ${streamerLabel}?`);
    $('#analytics-delete-modal-note').show();
    $('#analytics-delete-modal-cancel').show();
    $('#analytics-delete-modal-confirm').removeClass('is-loading').addClass('is-danger').text('Delete selected data').prop('disabled', false);
    $('#analytics-delete-modal-cancel').prop('disabled', false).focus();
}

function setStreamerDeleteSelectionMode(enabled) {
    streamerDeleteSelectionMode = enabled;
    selectedStreamerAnalytics.clear();
    lastStreamerCheckboxIndex = null;
    renderStreamers();
}

function handleStreamerAnalyticsSelection(event, index, streamer) {
    var isChecked = event.currentTarget.checked;
    if (event.shiftKey && lastStreamerCheckboxIndex !== null) {
        var start = Math.min(lastStreamerCheckboxIndex, index);
        var end = Math.max(lastStreamerCheckboxIndex, index);
        for (var currentIndex = start; currentIndex <= end; currentIndex++) {
            var currentStreamerName = streamersList[currentIndex].name;
            if (isChecked) selectedStreamerAnalytics.add(currentStreamerName);
            else selectedStreamerAnalytics.delete(currentStreamerName);
            $(`.streamer-delete-checkbox[data-index="${currentIndex}"]`).prop('checked', isChecked);
        }
    } else if (isChecked) {
        selectedStreamerAnalytics.add(streamer);
    } else {
        selectedStreamerAnalytics.delete(streamer);
    }

    lastStreamerCheckboxIndex = index;
    updateStreamerDeleteControls();
}

function updateStreamerDeleteControls() {
    var selectedCount = selectedStreamerAnalytics.size;
    var buttonLabel = streamerDeleteSelectionMode
        ? `Delete selected (${selectedCount})`
        : 'Delete analytics data';
    $('#delete-streamer-analytics .delete-streamer-button-label').text(buttonLabel);
    $('#delete-streamer-analytics')
        .prop('disabled', analyticsDeleteInProgress || streamersList.length === 0 || (streamerDeleteSelectionMode && selectedCount === 0))
        .attr('title', streamerDeleteSelectionMode
            ? 'Permanently delete the selected analytics data'
            : 'Select streamer analytics data to delete');
    $('#cancel-streamer-analytics-selection').toggle(streamerDeleteSelectionMode);
    $('#streamer-selection-hint').toggle(streamerDeleteSelectionMode);
}

// Generic in-page replacement for window.confirm(), used for destructive
// dashboard actions (e.g. removing a streamer/category). Browsers can block
// or auto-dismiss repeated native confirm() popups, which silently breaks
// these actions, so this renders an actual dialog in the page instead.
var confirmDialogCallback = null;

function showConfirmDialog(options) {
    confirmDialogCallback = options.onConfirm;
    $('#confirm-modal-title').text(options.title);
    $('#confirm-modal-message').text(options.message);
    $('#confirm-modal-confirm').text(options.confirmLabel || 'Remove');
    $('#confirm-modal').addClass('is-active').attr('aria-hidden', 'false');
    $('#confirm-modal-cancel').focus();
}

function closeConfirmDialog() {
    confirmDialogCallback = null;
    $('#confirm-modal').removeClass('is-active').attr('aria-hidden', 'true');
}

function closeAnalyticsDeleteModal() {
    if (analyticsDeleteInProgress) return;

    pendingDeleteStreamers = [];
    $('#analytics-delete-modal').removeClass('is-active is-error').attr('aria-hidden', 'true');
    $('#delete-streamer-analytics').focus();
}

function showAnalyticsDeleteError(message) {
    analyticsDeleteInProgress = false;
    pendingDeleteStreamers = [];
    $('#analytics-delete-modal').addClass('is-error');
    $('#analytics-delete-modal .analytics-modal-icon i').attr('class', 'fas fa-exclamation-triangle');
    $('#analytics-delete-modal-title').text('Could not delete analytics data');
    $('#analytics-delete-modal-message').text(message);
    $('#analytics-delete-modal-note').hide();
    $('#analytics-delete-modal-cancel').hide();
    $('#analytics-delete-modal-confirm').removeClass('is-loading is-danger').text('Close').prop('disabled', false).focus();
    updateStreamerDeleteControls();
}

function confirmStreamerAnalyticsDeletion() {
    if ($('#analytics-delete-modal').hasClass('is-error')) {
        closeAnalyticsDeleteModal();
        return;
    }
    if (pendingDeleteStreamers.length === 0 || analyticsDeleteInProgress) return;

    var streamers = pendingDeleteStreamers.slice();
    analyticsDeleteInProgress = true;
    $('#analytics-delete-modal-cancel').prop('disabled', true);
    $('#analytics-delete-modal-confirm').addClass('is-loading').prop('disabled', true);
    $('#delete-streamer-analytics').prop('disabled', true);
    var deleteRequests = streamers.map(streamer => $.ajax({
        url: `streamers/${encodeURIComponent(streamer)}`,
        method: 'DELETE'
    }));
    Promise.allSettled(deleteRequests).then(function (results) {
        var deletedStreamers = streamers.filter((_streamer, index) => results[index].status === 'fulfilled');
        var failedStreamers = streamers.filter((_streamer, index) => results[index].status === 'rejected');

        if (deletedStreamers.includes(currentStreamer)) {
            if (streamerRefreshTimeout) {
                clearTimeout(streamerRefreshTimeout);
                streamerRefreshTimeout = null;
            }
            streamerDataRequest++;
            safeStorage.removeItem("selectedStreamer");
            currentStreamer = null;
        }

        analyticsDeleteInProgress = false;
        $('#analytics-delete-modal-cancel').prop('disabled', false);
        if (failedStreamers.length === 0) {
            closeAnalyticsDeleteModal();
            streamerDeleteSelectionMode = false;
            selectedStreamerAnalytics.clear();
            lastStreamerCheckboxIndex = null;
        } else {
            selectedStreamerAnalytics = new Set(failedStreamers);
            var failureMessage = deletedStreamers.length > 0
                ? `Deleted ${deletedStreamers.length} selection(s), but ${failedStreamers.length} could not be deleted. Please try again.`
                : 'Unable to delete the selected analytics data. Please try again.';
            showAnalyticsDeleteError(failureMessage);
        }
        getStreamers();
    });
}

function sortStreamers() {
    streamersList = streamersList.sort((a, b) => {
        return (a[sortField] > b[sortField] ? 1 : -1) * (sortBy.includes("ascending") ? 1 : -1);
    });
}

function changeSortBy(option) {
    sortBy = option.innerText.trim();
    if (sortBy.includes("Points")) sortField = 'points'
    else if (sortBy.includes("Last activity")) sortField = 'last_activity'
    else sortField = 'name';
    lastStreamerCheckboxIndex = null;
    sortStreamers();
    renderStreamers();
    $('#sorting-by').text(sortBy);
    safeStorage.setItem("sort-by", sortBy);
}

function updateAnnotations() {
    if (!chartRendered || $('#points-panel').is(':hidden')) return;

    if ($('#annotations').prop("checked") === true) {
        clearAnnotations()
        if (annotations && annotations.length > 0)
            annotations.forEach((annotation, index) => {
                annotations[index]['id'] = `id-${index}`
                chart.addXaxisAnnotation(annotation, true)
            })
    } else clearAnnotations()
}

function renderPointsChart() {
    if (!chartRendered || $('#points-panel').is(':hidden')) return;

    var series = currentStreamer ? [{
        name: currentStreamer.replace(".json", ""),
        data: pointSeries
    }] : [];

    chart.updateOptions({ title: options.title }, false, false)
        .then(function () {
            return chart.updateSeries(series, true);
        })
        .then(function () {
            updateAnnotations();
        });
}

function clearAnnotations() {
    if (!chartRendered) return;

    if (annotations && annotations.length > 0)
        annotations.forEach((annotation, index) => {
            chart.removeAnnotation(annotation['id'])
        })
    chart.clearAnnotations();
}

var nowWatchingRefreshTimeout = null;

function renderNowWatching(entries) {
    var list = $('#now-watching-list');
    list.empty();

    if (!entries || entries.length === 0) {
        list.text('Not currently watching anything.');
        return;
    }

    entries.forEach(function (entry) {
        var line = $('<div class="now-watching-entry"></div>');

        if ((entry.reason === 'drops' || entry.reason === 'badge') && entry.game) {
            var label = entry.reason === 'badge' ? 'Badge campaign' : 'Drops';
            var link = $('<a href="#"></a>').text(
                `Currently watching ${entry.username} for ${label} — ${entry.game}`
            );
            link.on('click', function (e) {
                e.preventDefault();
                switchDashboardTab('drops');
                changeDropCategory(entry.game);
            });
            line.append(link);
        } else if (entry.reason === 'drops' || entry.reason === 'badge') {
            var label2 = entry.reason === 'badge' ? 'Badge campaign' : 'Drops';
            line.text(`Currently watching ${entry.username} for ${label2}`);
        } else {
            line.text(
                `Currently watching ${entry.username} for points (${entry.channel_points})`
            );
        }

        list.append(line);
    });
}

function getNowWatching() {
    $.getJSON('./now_watching', function (response) {
        renderNowWatching(response);
        if (nowWatchingRefreshTimeout) {
            clearTimeout(nowWatchingRefreshTimeout);
        }
        nowWatchingRefreshTimeout = setTimeout(getNowWatching, logPollInterval);
    }).fail(function () {
        renderNowWatching([]);
        if (nowWatchingRefreshTimeout) {
            clearTimeout(nowWatchingRefreshTimeout);
        }
        nowWatchingRefreshTimeout = setTimeout(getNowWatching, logPollInterval);
    });
}

function getDropsByCategory() {
    $.getJSON('./drops_by_category', function (response) {
        dropsLoaded = true;
        clearAnalyticsLoadError();
        console.debug('[analytics] Drops response', response);
        renderDropsByCategory(response);
        if (dropsRefreshTimeout) {
            clearTimeout(dropsRefreshTimeout);
        }
        dropsRefreshTimeout = setTimeout(function () {
            getDropsByCategory();
        }, refresh);
    }).fail(function (xhr, status, error) {
        dropsLoaded = false;
        showAnalyticsLoadError(
            `Drops failed to load (${xhr.status || status}): ${error || xhr.responseText || 'Unknown error'}`,
            xhr.responseText
        );
        renderDropsByCategory({ drops: [] });
        if (dropsRefreshTimeout) {
            clearTimeout(dropsRefreshTimeout);
        }
        dropsRefreshTimeout = setTimeout(function () {
            getDropsByCategory();
        }, refresh);
    });
}

function getDropTimestamp(drop) {
    if (drop && drop.x) {
        return drop.x;
    }
    if (drop && drop.datetime) {
        var parsed = Date.parse(drop.datetime);
        if (!isNaN(parsed)) return parsed;
    }
    return 0;
}

function getDropProgressPercent(drop) {
    if (!drop) return 0;

    var progress = Number(drop.percentage_progress);
    if (!isNaN(progress)) {
        return progress;
    }

    var currentMinutes = Number(drop.current_minutes_watched || 0);
    var requiredMinutes = Number(drop.minutes_required || 0);
    if (requiredMinutes > 0) {
        return (currentMinutes / requiredMinutes) * 100;
    }

    return 0;
}

function compareDropsForTable(a, b) {
    var primaryDiff = getDropTimestamp(b) - getDropTimestamp(a);
    if (primaryDiff !== 0) {
        return primaryDiff;
    }

    var progressDiff = getDropProgressPercent(a) - getDropProgressPercent(b);
    if (progressDiff !== 0) {
        return progressDiff;
    }

    return (b.current_minutes_watched || 0) - (a.current_minutes_watched || 0);
}

function getDropEndTimestamp(drop) {
    if (!drop) return -1;

    var rawEnd = drop.drop_end_at || drop.end_at || drop.ends_at || drop.endAt || null;
    if (rawEnd) {
        var parsed = Date.parse(rawEnd);
        if (!isNaN(parsed)) return parsed;

        // Handle legacy values missing timezone marker (treat as UTC).
        if (typeof rawEnd === 'string' && /^[0-9]{4}-[0-9]{2}-[0-9]{2}T/.test(rawEnd) && !rawEnd.endsWith('Z')) {
            parsed = Date.parse(`${rawEnd}Z`);
            if (!isNaN(parsed)) return parsed;
        }
    }
    return -1;
}

function getDropStatusSortPriority(drop, now) {
    var status = String(drop.status || '').toLowerCase();
    var currentMinutes = drop.current_minutes_watched || 0;
    var requiredMinutes = drop.minutes_required || 0;
    var endTimestamp = getDropEndTimestamp(drop);
    var isExpired = endTimestamp !== -1 && endTimestamp < now;
    var isCompleted = requiredMinutes > 0 && currentMinutes >= requiredMinutes;

    if (drop.failed_to_achieve === true || (isExpired && !isCompleted && status !== 'captured')) {
        return 3;
    }
    if (status === 'in_progress' || (!isCompleted && currentMinutes > 0)) {
        return 0;
    }
    if (status === 'captured' || isCompleted) {
        return 2;
    }
    return 1;
}

function getFilteredDropsForCategory(category) {
    var drops = (dropsState.categories[category] || []).slice();
    var now = Date.now();

    if (dropsFilter === 'active') {
        drops = drops.filter((drop) => {
            var endTs = getDropEndTimestamp(drop);
            return endTs === -1 || endTs >= now;
        });
    } else if (dropsFilter === 'last_30' || dropsFilter === 'last_60' || dropsFilter === 'last_90') {
        var days = parseInt(dropsFilter.split('_')[1], 10);
        var cutoff = now - (days * 24 * 60 * 60 * 1000);
        drops = drops.filter((drop) => getDropTimestamp(drop) >= cutoff);
    }

    // Collapse repeated snapshots so each drop appears once using the latest data.
    // Keyed on drop_id alone where available: the same drop_id can be recorded
    // under different item_name values across its lifecycle (a friendly display
    // name while actively watched vs. a shorter internal name from the periodic
    // full-inventory sync), so including item_name/item_art_url in the key let a
    // completed drop's later "captured" snapshot land under a different key than
    // its earlier "in_progress" snapshot - leaving a stale, never-collapsing
    // "in progress" ghost row next to the real completed one. Only fall back to
    // the old composite key when drop_id itself is missing.
    var dedupedDropsById = {};
    drops.forEach((drop) => {
        var dropKey = drop.drop_id
            ? ['id', drop.drop_id, category || 'unknown'].join('|')
            : [
                  '',
                  drop.item_art_url || '',
                  drop.item_name || 'unknown',
                  drop.campaign || 'unknown',
                  category || 'unknown',
              ].join('|');
        var existing = dedupedDropsById[dropKey];
        if (!existing || getDropTimestamp(drop) > getDropTimestamp(existing)) {
            // Keep a known expiry date when the newer snapshot is missing it.
            if (existing && !drop.drop_end_at && existing.drop_end_at) {
                drop.drop_end_at = existing.drop_end_at;
            }
            dedupedDropsById[dropKey] = drop;
        } else if (existing && !existing.drop_end_at && drop.drop_end_at) {
            existing.drop_end_at = drop.drop_end_at;
        }
    });
    drops = Object.values(dedupedDropsById);

    drops.sort((a, b) => {
        var aEndTimestamp = getDropEndTimestamp(a);
        var bEndTimestamp = getDropEndTimestamp(b);
        var aCampaignClosed = aEndTimestamp !== -1 && aEndTimestamp < now;
        var bCampaignClosed = bEndTimestamp !== -1 && bEndTimestamp < now;
        if (aCampaignClosed !== bCampaignClosed) {
            return aCampaignClosed ? 1 : -1;
        }

        var statusDiff = getDropStatusSortPriority(a, now) - getDropStatusSortPriority(b, now);
        if (statusDiff !== 0) {
            return statusDiff;
        }

        var progressDiff = getDropProgressPercent(b) - getDropProgressPercent(a);
        if (progressDiff !== 0) {
            return progressDiff;
        }

        return getDropTimestamp(b) - getDropTimestamp(a);
    });

    return drops;
}

function getVisibleDropCategories() {
    return dropsState.orderedCategories.filter((category) => {
        return getFilteredDropsForCategory(category).length > 0;
    });
}

function changeDropsFilter(value) {
    dropsFilter = value;
    dropsPage = 1;
    safeStorage.setItem('dropsFilter', value);
    renderDropCategoryList();
    renderDropRows();
}

function changeDropsPage(offset) {
    dropsPage += offset;
    renderDropRows();
}

function normalizeDropsData(response) {
    var grouped = (response && response.categories && typeof response.categories === 'object')
        ? response.categories
        : {};

    var drops = (response && Array.isArray(response.drops)) ? response.drops : [];

    if (Object.keys(grouped).length === 0 && drops.length > 0) {
        grouped = {};
        drops.forEach((drop) => {
            var category = drop.category || 'Unknown';
            if (!grouped[category]) grouped[category] = [];
            grouped[category].push(drop);
        });
    }

    Object.keys(grouped).forEach((category) => {
        grouped[category] = grouped[category].slice().sort(compareDropsForTable);
    });

    var orderedCategories = Object.keys(grouped).sort((a, b) => {
        var aLatest = grouped[a][0] ? (grouped[a][0].x || 0) : 0;
        var bLatest = grouped[b][0] ? (grouped[b][0].x || 0) : 0;
        if (bLatest !== aLatest) {
            return bLatest - aLatest;
        }

        var aProgress = grouped[a][0] ? getDropProgressPercent(grouped[a][0]) : 0;
        var bProgress = grouped[b][0] ? getDropProgressPercent(grouped[b][0]) : 0;
        return bProgress - aProgress;
    });

    return {
        categories: grouped,
        orderedCategories: orderedCategories
    };
}

function changeDropCategory(category) {
    currentDropCategory = category;
    dropsPage = 1;
    safeStorage.setItem('selectedDropCategory', category);
    renderDropCategoryList();
    renderDropRows();
}

function renderDropCategoryList() {
    var categoriesList = $('#drops-categories-list');
    categoriesList.empty();
    var visibleCategories = getVisibleDropCategories();

    if (visibleCategories.length === 0) {
        currentDropCategory = null;
        categoriesList.append('<li class="is-active"><a href="#">No categories yet</a></li>');
        return;
    }

    if (!currentDropCategory || visibleCategories.indexOf(currentDropCategory) === -1) {
        currentDropCategory = visibleCategories[0];
        dropsPage = 1;
    }

    visibleCategories.forEach((category) => {
        var isActive = currentDropCategory === category;
        var activeClass = isActive ? 'is-active' : '';
        var dropsCount = getFilteredDropsForCategory(category).length;
        var categoryLabel = `${category} (${dropsCount} ${dropsCount === 1 ? 'drop' : 'drops'})`;

        categoriesList.append(`
            <li class="${activeClass}">
                <a href="#" data-category="${escapeHtml(category)}">
                    ${escapeHtml(categoryLabel)}
                </a>
            </li>
        `);
    });

    $('#drops-categories-list a[data-category]').off('click').on('click', function (e) {
        e.preventDefault();
        changeDropCategory($(this).data('category'));
    });
}

function renderDropRows() {
    var dropsItems = $('#drops-items');
    var dropsCategoryTitle = $('#drops-category-title');
    dropsItems.empty();

    if (!currentDropCategory || !dropsState.categories[currentDropCategory]) {
        dropsCategoryTitle.text('');
        dropsItems.append('<div class="drops-empty">No drop events yet.</div>');
        $('#drops-pagination').hide();
        return;
    }

    var drops = getFilteredDropsForCategory(currentDropCategory);
    dropsCategoryTitle.text(`${currentDropCategory} (${drops.length})`);

    if (drops.length === 0) {
        dropsItems.append('<div class="drops-empty">No drops match this filter in this category.</div>');
        $('#drops-pagination').hide();
        return;
    }

    var totalPages = Math.max(1, Math.ceil(drops.length / dropsPerPage));
    if (dropsPage < 1) dropsPage = 1;
    if (dropsPage > totalPages) dropsPage = totalPages;

    var pageStart = (dropsPage - 1) * dropsPerPage;
    var pageDrops = drops.slice(pageStart, pageStart + dropsPerPage);

    $('#drops-page-info').text(`Page ${dropsPage} / ${totalPages}`);
    $('#drops-prev').prop('disabled', dropsPage <= 1);
    $('#drops-next').prop('disabled', dropsPage >= totalPages);
    $('#drops-pagination').toggle(totalPages > 1);

    pageDrops.forEach((drop) => {
        var timestamp = drop.datetime || '';
        var status = String(drop.status || '').toLowerCase();
        var currentMinutes = drop.current_minutes_watched || 0;
        var requiredMinutes = drop.minutes_required || 0;
        var displayedMinutes = Math.min(currentMinutes, requiredMinutes || currentMinutes);
        var progressPercent = Math.max(0, Math.min(100, drop.percentage_progress || 0));
        var progress = `${displayedMinutes}/${requiredMinutes} minutes (${progressPercent}%)`;
        var artUrl = drop.item_art_url || '';
        var itemName = drop.item_name || '';
        var campaign = drop.campaign || '';
        var streamer = drop.streamer || '';
        var dropEndAt = drop.drop_end_at || '';
        var endAtTimestamp = Date.parse(dropEndAt);
        var isExpired = !isNaN(endAtTimestamp) && Date.now() > endAtTimestamp;
        var isCompleted = requiredMinutes > 0 && currentMinutes >= requiredMinutes;
        var isFailed = drop.failed_to_achieve === true || (isExpired && !isCompleted && status !== 'captured');
        var statusClass = status === 'captured' ? 'is-captured' : 'is-progress';
        var expiresAtText = !isNaN(endAtTimestamp) ? new Date(endAtTimestamp).toLocaleString() : '';
        var progressBarClass = statusClass;
        var statusTooltip = '';
        var statusDisplay = status === 'captured'
            ? 'Captured'
            : status === 'in_progress'
                ? 'In Progress'
                : String(status || 'Unknown').replace(/_/g, ' ');

        if (isFailed) {
            statusDisplay = 'Expired';
            statusClass = 'is-failed';
            progressBarClass = 'is-failed';
            statusTooltip = 'Drop expired before completion';
        }

        var metadataSpans = [
            `<span>${escapeHtml(progress)}</span>`,
            campaign ? `<span>${escapeHtml(campaign)}</span>` : '',
            streamer ? `<span>${escapeHtml(streamer)}</span>` : '',
            expiresAtText ? `<span>Expires: ${escapeHtml(expiresAtText)}</span>` : '',
            timestamp ? `<span>${escapeHtml(timestamp)}</span>` : '',
        ].filter(Boolean).join('');

        var failedIcon = isFailed
            ? '<span class="drop-failed-icon">✕</span>'
            : '';
        var statusTitle = statusTooltip
            ? ` title="${escapeHtml(statusTooltip)}"`
            : '';

        var artHtml = artUrl
            ? `<img class="drop-art-thumb" src="${escapeHtml(artUrl)}" alt="${escapeHtml(itemName)} art">`
            : '<div class="drop-art-placeholder"><i class="fa fa-gift" aria-hidden="true"></i></div>';

        dropsItems.append(`
            <div class="drop-row">
                <div class="drop-row-art">${artHtml}</div>
                <div class="drop-row-body">
                    <div class="drop-row-top">
                        <div class="drop-item-name">${failedIcon}${escapeHtml(itemName || 'Unknown Drop')}</div>
                        <span class="drop-status ${statusClass}"${statusTitle}>${escapeHtml(statusDisplay)}</span>
                    </div>
                    <div class="drop-progress-block">
                        <div class="drop-progress-bar" aria-hidden="true">
                            <div class="drop-progress-fill ${progressBarClass}" style="width: ${progressPercent}%;"></div>
                        </div>
                        <div class="drop-progress-label">${escapeHtml(progress)}</div>
                    </div>
                    <div class="drop-row-meta">
                        ${metadataSpans}
                    </div>
                </div>
            </div>
        `);
    });
}

function renderDropsByCategory(response) {
    dropsState = normalizeDropsData(response);

    var savedDropCategory = safeStorage.getItem('selectedDropCategory');
    if (savedDropCategory && dropsState.categories[savedDropCategory]) {
        currentDropCategory = savedDropCategory;
    }

    if (!currentDropCategory || !dropsState.categories[currentDropCategory]) {
        currentDropCategory = dropsState.orderedCategories.length > 0
            ? dropsState.orderedCategories[0]
            : null;
    }

    renderDropCategoryList();
    renderDropRows();
}

function escapeHtml(text) {
    return String(text)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
}

function renderWebConfig(config) {
    webConfigState = config;
    renderConfiguredStreamers(config.streamers || []);
    renderConfiguredCategories(config.categories || []);
    $('#category-limit').val(config.category.limit);
    $('#category-sort').val(config.category.sort);
    $('#category-refresh').val(config.category.refresh_interval_hours);
    $('#category-drops-enabled').prop('checked', config.category.drops_enabled);
    renderSourceSettings(config.sources || {}, config.source_order || []);
    $('#console-log-level').val(config.logging.console_level);
    $('#file-log-level').val(config.logging.file_level);
    $('#daily-report-enabled').prop('checked', config.logging.daily_report);
    $('#daily-report-time').val(config.logging.daily_report_time);
    $('#update-check-enabled').prop('checked', config.updates.enabled);
    $('#update-check-interval').val(config.updates.interval_hours);
    $('#update-check-startup-only').prop('checked', config.updates.startup_only);
    toggleUpdateInterval();
    renderNotificationSettings(config);
}

var configuredStreamersSortable = null;
var configuredCategoriesSortable = null;
var sourceSettingsSortable = null;

// Creates a Sortable instance once per container (re-rendering only
// replaces the container's children, not the container element itself, so
// re-running Sortable.create on every render would stack duplicate
// instances on the same DOM node).
function makeSortable(containerEl, onEnd) {
    return Sortable.create(containerEl, {
        handle: '.drag-handle',
        ghostClass: 'sortable-ghost',
        onEnd: function (evt) {
            // Dropping a row back where it started still fires onEnd; skip
            // the save so a no-op drag doesn't rewrite config.py or pop a
            // "order was updated" toast.
            if (onEnd && evt.oldIndex !== evt.newIndex) {
                onEnd(evt);
            }
        }
    });
}

function renderConfiguredStreamers(streamers) {
    var container = $('#configured-streamers').empty();
    if (!configuredStreamersSortable) {
        configuredStreamersSortable = makeSortable(container[0], function () {
            var reordered = $('#configured-streamers .config-streamer').map(function () {
                return $(this).attr('data-username');
            }).get();
            updateWebConfig({ action: 'reorder_streamers', usernames: reordered }, 'Streamer order was updated.');
        });
    }
    if (!streamers.length) {
        container.append($('<span>').addClass('config-empty').text('No streamers configured.'));
        return;
    }
    streamers.forEach(function (streamer) {
        var settings = streamer.settings || {};
        var item = $('<details>').addClass('config-item config-streamer').attr('data-username', streamer.username);
        var summary = $('<summary>');
        summary.append($('<span>').addClass('drag-handle icon is-small').attr('aria-hidden', 'true').html('<i class="fas fa-grip-vertical"></i>'));
        summary.append($('<strong>').text(streamer.username));
        summary.append($('<span>').addClass('icon is-small').html('<i class="fas fa-cog" aria-hidden="true"></i>'));
        item.append(summary);
        var controls = $('<div>').addClass('streamer-settings config-settings-grid');
        [
            ['favorite', 'Favorite'],
            ['make_predictions', 'Predictions'],
            ['follow_raid', 'Follow raids'],
            ['claim_drops', 'Claim Drops'],
            ['claim_moments', 'Claim moments']
        ].forEach(function (setting) {
            var label = $('<label>').addClass('checkbox config-checkbox').text(` ${setting[1]}`);
            label.prepend($('<input>').attr('type', 'checkbox').attr('data-setting', setting[0]).prop('checked', settings[setting[0]] === true));
            controls.append(label);
        });
        var chat = $('<select>').attr('data-setting', 'chat');
        ['ALWAYS', 'NEVER', 'ONLINE', 'OFFLINE'].forEach(function (value) {
            chat.append($('<option>').val(value).text(value));
        });
        chat.val(settings.chat || 'ONLINE');
        controls.append($('<label>').addClass('label').text('Chat presence').append($('<span>').addClass('select is-fullwidth').append(chat)));
        controls.append($('<label>').addClass('label').text('Points limit').append(
            $('<input>').addClass('input').attr({ type: 'number', min: 0, placeholder: 'No limit', 'data-setting': 'points_limit' }).val(settings.points_limit)
        ));
        var actions = $('<div>').addClass('config-item-actions');
        actions.append($('<button>').addClass('button is-small is-link save-streamer-settings').attr('type', 'button').text('Save settings'));
        actions.append($('<button>').addClass('button is-small is-danger remove-streamer').attr('type', 'button').text('Remove'));
        controls.append(actions);
        item.append(controls);
        container.append(item);
    });
    $('.save-streamer-settings').off('click').on('click', saveStreamerSettings);
    $('.remove-streamer').off('click').on('click', function () {
        var username = $(this).closest('.config-streamer').attr('data-username');
        showConfirmDialog({
            title: 'Remove streamer?',
            message: `Remove ${username} from the miner configuration?`,
            confirmLabel: 'Remove',
            onConfirm: function () {
                updateWebConfig({ action: 'remove', kind: 'streamers', value: username }, `${username} was removed.`);
            }
        });
    });
    // Prevent a plain click (no drag) on the handle from toggling the
    // enclosing <details> row open/closed via its <summary> ancestor.
    $('#configured-streamers .drag-handle').off('click').on('click', function (event) {
        event.preventDefault();
    });
}

function renderConfiguredCategories(categories) {
    var container = $('#configured-categories').empty();
    if (!configuredCategoriesSortable) {
        configuredCategoriesSortable = makeSortable(container[0], function () {
            var reordered = $('#configured-categories .config-item-name').map(function () {
                return $(this).text();
            }).get();
            updateWebConfig({ action: 'reorder_categories', categories: reordered }, 'Category order was updated.');
        });
    }
    if (!categories.length) {
        container.append($('<span>').addClass('config-empty').text('No categories configured.'));
        return;
    }
    categories.forEach(function (category) {
        var row = $('<div>').addClass('config-item config-category');
        row.append($('<span>').addClass('drag-handle icon is-small').attr('aria-hidden', 'true').html('<i class="fas fa-grip-vertical"></i>'));
        row.append($('<span>').addClass('config-item-name').text(category));
        var actions = $('<div>').addClass('config-item-actions');
        actions.append($('<button>').addClass('button is-small is-danger remove-category').attr('type', 'button').text('Remove'));
        row.append(actions);
        container.append(row);
    });
    $('.remove-category').off('click').on('click', function () {
        var category = $(this).closest('.config-category').find('.config-item-name').text();
        showConfirmDialog({
            title: 'Remove category?',
            message: `Remove ${category} from the miner configuration?`,
            confirmLabel: 'Remove',
            onConfirm: function () {
                updateWebConfig({ action: 'remove', kind: 'categories', value: category }, `${category} was removed.`);
            }
        });
    });
}

var SOURCE_LABELS = {
    streamers: 'Explicit streamers',
    followers: 'Followed channels',
    categories: 'Categories',
    badges: 'Badge campaigns',
    wildcard_categories: 'Wildcard categories'
};

function renderSourceSettings(sources, sourceOrder) {
    var container = $('#source-settings-list').empty();
    if (!sourceSettingsSortable) {
        sourceSettingsSortable = makeSortable(container[0]);
    }
    var order = sourceOrder.length ? sourceOrder : Object.keys(SOURCE_LABELS);
    order.forEach(function (source) {
        var row = $('<div>').addClass('config-item config-source').attr('data-source-row', source);
        row.append($('<span>').addClass('drag-handle icon is-small').attr('aria-hidden', 'true').html('<i class="fas fa-grip-vertical"></i>'));
        var label = $('<label>').addClass('checkbox').text(` ${SOURCE_LABELS[source] || source}`);
        label.prepend($('<input>').attr({ type: 'checkbox', 'data-source': source }).prop('checked', sources[source] === true));
        row.append(label);
        container.append(row);
    });
}

// Renders save/error feedback as a stack of temporary toasts (top-right,
// position: fixed) instead of a single notification the user has to scroll
// down to see. Multiple toasts can be visible at once; each manages its own
// dismiss timer/animation independently.
function showConfigMessage(message, isError) {
    var toast = $('<div>')
        .addClass('toast notification')
        .toggleClass('is-danger', isError)
        .toggleClass('is-success', !isError)
        .attr('role', isError ? 'alert' : 'status');
    toast.append($('<span>').addClass('toast-message').text(message));
    var closeButton = $('<button>')
        .addClass('toast-close')
        .attr({ type: 'button', 'aria-label': 'Dismiss notification' })
        .html('&times;');
    toast.append(closeButton);

    var dismissed = false;
    function dismiss() {
        if (dismissed) return;
        dismissed = true;
        toast.removeClass('toast-visible');
        setTimeout(function () { toast.remove(); }, 200);
    }
    closeButton.on('click', dismiss);

    $('#toast-container').append(toast);
    requestAnimationFrame(function () {
        toast.addClass('toast-visible');
    });

    // Success toasts clear themselves; errors stay until dismissed so a
    // problem the user needs to act on can't silently disappear unread.
    if (!isError) {
        setTimeout(dismiss, 5000);
    }
}

function loadWebConfig() {
    $.getJSON('/config').done(function (config) {
        configLoaded = true;
        renderWebConfig(config);
    }).fail(function (xhr) {
        showConfigMessage(xhr.responseJSON && xhr.responseJSON.error
            ? xhr.responseJSON.error : 'Unable to load configuration.', true);
    });
}

function addWebConfigValue(kind, input) {
    var value = input.val().trim();
    if (!value) return;
    var button = input.closest('form').find('button').prop('disabled', true);
    $.ajax({
        url: '/config',
        method: 'POST',
        contentType: 'application/json',
        data: JSON.stringify({ action: 'add', kind: kind, value: value })
    }).done(function (config) {
        renderWebConfig(config);
        input.val('');
        showConfigMessage(`${value} was added. The miner will reload it shortly.`, false);
    }).fail(function (xhr) {
        showConfigMessage(xhr.responseJSON && xhr.responseJSON.error
            ? xhr.responseJSON.error : 'Unable to update configuration.', true);
    }).always(function () {
        button.prop('disabled', false);
    });
}

function updateWebConfig(payload, successMessage, button) {
    if (button) button.prop('disabled', true);
    return $.ajax({
        url: '/config',
        method: 'POST',
        contentType: 'application/json',
        data: JSON.stringify(payload)
    }).done(function (config) {
        renderWebConfig(config);
        showConfigMessage(successMessage, false);
    }).fail(function (xhr) {
        showConfigMessage(xhr.responseJSON && xhr.responseJSON.error
            ? xhr.responseJSON.error : 'Unable to update configuration.', true);
        // The write didn't take effect server-side (e.g. a failed drag
        // reorder), but the DOM may already reflect the attempted change
        // (SortableJS moves the row before onEnd fires). Resync from the
        // server so the displayed order doesn't silently diverge from
        // config.py's actual contents.
        loadWebConfig();
    }).always(function () {
        if (button) button.prop('disabled', false);
    });
}

function saveStreamerSettings() {
    var button = $(this);
    var item = button.closest('.config-streamer');
    var pointsValue = item.find('[data-setting="points_limit"]').val();
    var settings = { points_limit: pointsValue === '' ? null : Number(pointsValue) };
    item.find('input[type="checkbox"][data-setting]').each(function () {
        settings[$(this).data('setting')] = $(this).prop('checked');
    });
    settings.chat = item.find('[data-setting="chat"]').val();
    var username = item.attr('data-username');
    updateWebConfig({ action: 'update_streamer', username: username, settings: settings }, `${username} settings were saved.`, button);
}

function saveCategorySettings(event) {
    event.preventDefault();
    var button = $(this).find('button[type="submit"]');
    updateWebConfig({
        action: 'update_category',
        values: {
            limit: Number($('#category-limit').val()),
            sort: $('#category-sort').val(),
            refresh_interval_hours: Number($('#category-refresh').val()),
            drops_enabled: $('#category-drops-enabled').prop('checked')
        }
    }, 'Category settings were saved. Restart the miner to apply them.', button);
}

function saveSourceSettings(event) {
    event.preventDefault();
    var values = {};
    $('[data-source]').each(function () { values[$(this).data('source')] = $(this).prop('checked'); });
    var order = $('#source-settings-list .config-source').map(function () {
        return $(this).data('source-row');
    }).get();
    var button = $(this).find('button[type="submit"]');
    updateWebConfig({ action: 'update_sources', values: values, order: order }, 'Stream sources were saved. Restart the miner to apply them.', button);
}

function saveLoggingSettings(event) {
    event.preventDefault();
    var button = $(this).find('button[type="submit"]');
    updateWebConfig({
        action: 'update_logging',
        values: {
            console_level: $('#console-log-level').val(),
            file_level: $('#file-log-level').val(),
            daily_report: $('#daily-report-enabled').prop('checked'),
            daily_report_time: $('#daily-report-time').val()
        }
    }, 'Logging settings were saved. Restart the miner to apply them.', button);
}

function toggleUpdateInterval() {
    $('#update-check-interval').prop(
        'disabled',
        $('#update-check-startup-only').prop('checked')
    );
}

function saveUpdateSettings(event) {
    event.preventDefault();
    var button = $(this).find('button[type="submit"]');
    var startupOnly = $('#update-check-startup-only').prop('checked');
    updateWebConfig({
        action: 'update_updates',
        values: {
            enabled: $('#update-check-enabled').prop('checked'),
            interval_hours: startupOnly ? undefined : Number($('#update-check-interval').val()),
            startup_only: startupOnly
        }
    }, 'Update-check settings were saved. Restart the miner to apply them.', button);
}

function renderNotificationSettings(config) {
    var container = $('#notification-settings').empty();
    Object.keys(config.notification_schemas || {}).forEach(function (provider) {
        var schema = config.notification_schemas[provider];
        var state = config.notifications[provider];
        var card = $('<details>').addClass('notification-config config-item').attr('data-provider', provider);
        var summary = $('<summary>').append($('<strong>').text(provider.charAt(0).toUpperCase() + provider.slice(1)));
        summary.append($('<span>').addClass('tag').text(state.enabled ? 'Enabled' : 'Disabled'));
        card.append(summary);
        var fields = $('<div>').addClass('config-settings-grid notification-fields');
        var enabled = $('<input>').attr({ type: 'checkbox', 'data-notification-enabled': true }).prop('checked', state.enabled);
        fields.append($('<label>').addClass('checkbox config-checkbox').append(enabled).append(' Enabled'));
        schema.fields.forEach(function (name) {
            fields.append(buildNotificationField(name, state.fields[name], false, false, config.notification_event_options));
        });
        schema.secrets.forEach(function (name) {
            fields.append(buildNotificationField(name, '', true, state.secrets[name] === true, config.notification_event_options));
        });
        var actions = $('<div>').addClass('config-item-actions notification-actions');
        actions.append($('<button>').addClass('button is-small is-link save-notification').attr('type', 'button').text('Save notification'));
        actions.append($('<button>')
            .addClass('button is-small test-notification')
            .attr('type', 'button')
            .prop('disabled', state.test_available !== true)
            .text('Send test'));
        fields.append(actions);
        card.append(fields);
        container.append(card);
    });
    $('.save-notification').off('click').on('click', saveNotificationSettings);
    $('.test-notification').off('click').on('click', sendTestNotification);
    $('.event-capsule').off('click').on('click', function () {
        var capsule = $(this);
        var selected = capsule.attr('aria-pressed') !== 'true';
        capsule.attr('aria-pressed', selected ? 'true' : 'false');
        capsule.toggleClass('is-link', selected);
    });
}

function buildNotificationField(name, value, secret, configured, eventOptions) {
    var label = $('<label>').addClass('label').text(name.replace(/_/g, ' '));
    var booleanFields = ['disable_notification', 'use_ssl', 'starttls'];
    var arrayFields = ['events', 'recipients', 'tags'];
    var numberFields = ['chat_id', 'message_thread_id', 'port', 'priority'];
    var input;
    if (name === 'events') {
        var selectedEvents = Array.isArray(value) ? value : [];
        var eventField = $('<div>').addClass('notification-event-field');
        var capsules = $('<div>').addClass('event-capsules').attr({ 'data-field': name, 'data-array': 'true' });
        (eventOptions || []).forEach(function (eventName) {
            var selected = selectedEvents.indexOf(eventName) !== -1;
            capsules.append($('<button>')
                .addClass('button is-small event-capsule')
                .toggleClass('is-link', selected)
                .attr({ type: 'button', 'data-event': eventName, 'aria-pressed': selected ? 'true' : 'false' })
                .text(eventName.replace(/_/g, ' ')));
        });
        return eventField.append($('<span>').addClass('label').text('events')).append(capsules);
    }
    if (booleanFields.indexOf(name) !== -1) {
        input = $('<input>').attr({ type: 'checkbox', 'data-field': name }).prop('checked', value === true);
        return $('<label>').addClass('checkbox config-checkbox').append(input).append(` ${name.replace(/_/g, ' ')}`);
    }
    input = $('<input>').addClass('input').attr({
        type: secret ? 'password' : (numberFields.indexOf(name) !== -1 ? 'number' : 'text'),
        'data-field': name,
        'data-secret': secret ? 'true' : 'false',
        'data-array': arrayFields.indexOf(name) !== -1 ? 'true' : 'false',
        autocomplete: secret ? 'new-password' : 'off',
        placeholder: secret && configured ? 'Configured — leave blank to keep' : ''
    }).val(arrayFields.indexOf(name) !== -1 && Array.isArray(value) ? value.join(', ') : (value == null ? '' : value));
    return label.append(input);
}

function saveNotificationSettings() {
    var button = $(this);
    var card = button.closest('.notification-config');
    var values = { enabled: card.find('[data-notification-enabled]').prop('checked') };
    card.find('[data-field]').each(function () {
        var input = $(this);
        var name = input.data('field');
        var value;
        if (input.hasClass('event-capsules')) {
            value = input.find('.event-capsule[aria-pressed="true"]').map(function () {
                return $(this).data('event');
            }).get();
        } else {
            value = input.attr('type') === 'checkbox' ? input.prop('checked') : input.val().trim();
        }
        if (input.data('secret') && value === '') return;
        if (name === 'message_thread_id' && value === '') value = null;
        if (input.data('array') && !Array.isArray(value)) value = value ? value.split(',').map(item => item.trim()).filter(Boolean) : [];
        if (input.attr('type') === 'number' && value !== '' && value !== null) value = Number(value);
        values[name] = value;
    });
    var provider = card.data('provider');
    updateWebConfig({ action: 'update_notification', provider: provider, values: values }, `${provider} notification settings were saved. Restart the miner to apply them.`, button);
}

function sendTestNotification() {
    var button = $(this).prop('disabled', true);
    var provider = button.closest('.notification-config').data('provider');
    $.ajax({
        url: `/config/notifications/${encodeURIComponent(provider)}/test`,
        method: 'POST'
    }).done(function (response) {
        showConfigMessage(response.message || 'Test notification sent.', false);
    }).fail(function (xhr) {
        showConfigMessage(xhr.responseJSON && xhr.responseJSON.error
            ? xhr.responseJSON.error : 'Unable to send test notification.', true);
    }).always(function () {
        button.prop('disabled', false);
    });
}

$('.dropdown').click(() => {
    $('.dropdown').toggleClass('is-active');
});

// Input date
$('#startDate').change(() => {
    startDate = new Date(`${$('#startDate').val()}T00:00:00`);
    getStreamerData(currentStreamer);
});
$('#endDate').change(() => {
    endDate = new Date(`${$('#endDate').val()}T00:00:00`);
    getStreamerData(currentStreamer);
});
