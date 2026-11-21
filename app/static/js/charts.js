/**
 * TMA Platform — Dynamic Chart.js Dashboards (Milestone P14; TDR v2 §15).
 * Renders interactive line, doughnut, and bar charts with branded palettes.
 */

window.TMACharts = {
  palette: {
    primary: "#1F4FD8",
    primaryLight: "#93C5FD",
    success: "#10B981",
    warning: "#F59E0B",
    danger: "#EF4444",
    purple: "#8B5CF6",
    gray: "#6B7280",
    bgMuted: "#F3F4F6",
  },

  /**
   * Initializes a line chart for booking trends over time.
   */
  initBookingsLineChart: function (canvasId, labels, dataPoints) {
    const canvas = document.getElementById(canvasId);
    if (!canvas || typeof Chart === "undefined") return null;

    return new Chart(canvas, {
      type: "line",
      data: {
        labels: labels || [],
        datasets: [
          {
            label: "Confirmed Bookings",
            data: dataPoints || [],
            borderColor: this.palette.primary,
            backgroundColor: "rgba(31, 79, 216, 0.08)",
            borderWidth: 2.5,
            fill: true,
            tension: 0.35,
            pointBackgroundColor: this.palette.primary,
            pointRadius: 4,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: "#0F172A",
            padding: 10,
            cornerRadius: 6,
          },
        },
        scales: {
          x: { grid: { display: false } },
          y: {
            beginAtZero: true,
            ticks: { precision: 0 },
            grid: { color: "#E2E8F0" },
          },
        },
      },
    });
  },

  /**
   * Initializes a doughnut chart for trek status distribution.
   */
  initTreksStatusChart: function (canvasId, countsByStatus) {
    const canvas = document.getElementById(canvasId);
    if (!canvas || typeof Chart === "undefined") return null;

    const labels = ["Open", "Pending", "Completed", "Closed"];
    const data = [
      countsByStatus.open || 0,
      countsByStatus.pending || 0,
      countsByStatus.completed || 0,
      countsByStatus.closed || 0,
    ];

    return new Chart(canvas, {
      type: "doughnut",
      data: {
        labels: labels,
        datasets: [
          {
            data: data,
            backgroundColor: [
              this.palette.success,
              this.palette.warning,
              this.palette.primary,
              this.palette.gray,
            ],
            borderWidth: 2,
            borderColor: "#FFFFFF",
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        cutout: "70%",
        plugins: {
          legend: {
            position: "bottom",
            labels: { boxWidth: 12, padding: 15 },
          },
        },
      },
    });
  },

  /**
   * Initializes bar chart for participant distribution per trek.
   */
  initParticipantsBarChart: function (canvasId, labels, counts) {
    const canvas = document.getElementById(canvasId);
    if (!canvas || typeof Chart === "undefined") return null;

    return new Chart(canvas, {
      type: "bar",
      data: {
        labels: labels || [],
        datasets: [
          {
            label: "Participants",
            data: counts || [],
            backgroundColor: this.palette.primary,
            borderRadius: 4,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          x: { grid: { display: false } },
          y: {
            beginAtZero: true,
            ticks: { precision: 0 },
            grid: { color: "#E2E8F0" },
          },
        },
      },
    });
  },
};
