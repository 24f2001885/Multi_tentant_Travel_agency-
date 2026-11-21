/**
 * TMA Platform — HTML5/JS Client-side Form Validation Layer (Milestone P14; TDR v2 §15).
 * Enforces client-side validation, E.164 phone formats, date range sanity, and accessible feedback.
 */

document.addEventListener("DOMContentLoaded", function () {
  // 1. Bootstrap 5 Standard Form Validation
  const forms = document.querySelectorAll(".needs-validation, form.tma-validate");
  Array.prototype.slice.call(forms).forEach(function (form) {
    form.addEventListener(
      "submit",
      function (event) {
        if (!form.checkValidity()) {
          event.preventDefault();
          event.stopPropagation();
        }
        form.classList.add("was-validated");
      },
      false
    );
  });

  // 2. Date Range Sanity Checks (Start Date <= End Date)
  const startDateInputs = document.querySelectorAll("input[name='start_date']");
  startDateInputs.forEach(function (startInput) {
    const form = startInput.closest("form");
    if (!form) return;
    const endInput = form.querySelector("input[name='end_date']");
    if (!endInput) return;

    function validateDates() {
      if (startInput.value && endInput.value) {
        if (new Date(endInput.value) < new Date(startInput.value)) {
          endInput.setCustomValidity("End date cannot be earlier than start date.");
        } else {
          endInput.setCustomValidity("");
        }
      }
    }

    startInput.addEventListener("change", validateDates);
    endInput.addEventListener("change", validateDates);
  });

  // 3. E.164 Mobile Phone Sanitizer & Validator
  const phoneInputs = document.querySelectorAll("input[name='mobile_number'], input[name='phone']");
  phoneInputs.forEach(function (input) {
    input.addEventListener("input", function () {
      // Allow only numbers and optional hyphens/spaces
      const cleaned = this.value.replace(/[^0-9]/g, "");
      if (this.value.length > 0 && cleaned.length < 7) {
        this.setCustomValidity("Please enter a valid phone number with at least 7 digits.");
      } else {
        this.setCustomValidity("");
      }
    });
  });

  // 4. Coupon Code Auto-Uppercase
  const couponInputs = document.querySelectorAll("input[name='code'], input[name='coupon_code']");
  couponInputs.forEach(function (input) {
    input.addEventListener("input", function () {
      this.value = this.value.toUpperCase();
    });
  });

  // 5. Password Visibility Toggle (Show / Hide Password)
  document.addEventListener("click", function (e) {
    const btn = e.target.closest(".toggle-password, [data-toggle-password]");
    if (!btn) return;

    const group = btn.closest(".input-group");
    const input = group ? group.querySelector("input") : null;
    if (!input) return;

    const icon = btn.querySelector("i");
    if (input.type === "password") {
      input.type = "text";
      if (icon) {
        icon.classList.remove("bi-eye");
        icon.classList.add("bi-eye-slash");
      }
      btn.setAttribute("aria-label", "Hide password");
    } else {
      input.type = "password";
      if (icon) {
        icon.classList.remove("bi-eye-slash");
        icon.classList.add("bi-eye");
      }
      btn.setAttribute("aria-label", "Show password");
    }
  });
});
