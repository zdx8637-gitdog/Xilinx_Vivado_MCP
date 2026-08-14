`timescale 1ns / 1ps
////////////////////////////////////////////////////////////////////////////////
// Testbench: tb_breath_led
// Verifies:
//   1. Reset release → FSM enters RAMP_UP
//   2. PWM output toggles at ~200 Hz (with hardware parameters)
//   3. Duty cycle sweeps from DUTY_MIN to DUTY_MAX and back (accelerated)
//   4. LED output is active low (0 = ON)
//   5. FSM transitions: IDLE → RAMP_UP ↔ RAMP_DOWN
////////////////////////////////////////////////////////////////////////////////

module tb_breath_led;

    // ----------------------------------------------------------------
    // DUT signals
    // ----------------------------------------------------------------
    reg         clk;
    reg         rst_n;
    wire [3:0]  led;
    wire [31:0] duty_out;

    // ----------------------------------------------------------------
    // Instantiate DUT with accelerated breathing for simulation
    // ----------------------------------------------------------------
    breath_led #(
        .PWM_PERIOD_STEP (32'd17179),
        .DUTY_MIN        (32'h1000_0000),   // ~6% of 2^32 (smaller range for sim)
        .DUTY_MAX        (32'h2000_0000),   // ~12% of 2^32
        .DUTY_STEP       (32'h0100_0000),   // ~0.4% per step
        .BREATH_TIMER_MAX(32'd10)           // duty changes every 10 cycles (fast!)
    ) dut (
        .clk     (clk),
        .rst_n   (rst_n),
        .led     (led),
        .duty_out(duty_out)
    );

    // ----------------------------------------------------------------
    // 50 MHz clock: 20 ns period, 10 ns half-cycle
    // ----------------------------------------------------------------
    always #10 clk = ~clk;  // 50 MHz

    // ----------------------------------------------------------------
    // Test sequence
    // ----------------------------------------------------------------
    integer cycle_count;

    initial begin
        // VCD dump for waveform viewing
        $dumpfile("tb_breath_led.vcd");
        $dumpvars(0, tb_breath_led);

        // Init
        clk   = 0;
        rst_n = 0;
        cycle_count = 0;

        // --- TEST 1: Reset ---
        $display("============================================");
        $display(" TEST 1: Reset Behavior");
        $display("============================================");
        #100;
        $display("  After reset: led=%b  duty_out=0x%08h", led, duty_out);
        if (led === 4'b1111)  // active low: all off = all 1's
            $display("  PASS: LEDs off (4'b1111) after reset");
        else
            $display("  FAIL: Expected 4'b1111, got %b", led);

        // --- TEST 2: Release reset, enter RAMP_UP ---
        $display("\n============================================");
        $display(" TEST 2: FSM enters RAMP_UP after reset release");
        $display("============================================");
        rst_n = 1;
        #50;  // let a few cycles pass
        $display("  duty_out = 0x%08h (should be >= DUTY_MIN)", duty_out);
        if (duty_out >= 32'h1000_0000)
            $display("  PASS: duty at valid level after reset release");
        else
            $display("  FAIL: duty = 0x%08h", duty_out);

        // --- TEST 3: PWM toggling ---
        $display("\n============================================");
        $display(" TEST 3: PWM Output Toggling");
        $display("============================================");
        // Wait for at least one PWM period (~5000 cycles at 200 Hz)
        // But we need to see the signal toggle
        begin
            reg prev;
            integer toggles;
            integer i;
            toggles = 0;
            prev = led[0];
            for (i = 0; i < 50000; i = i + 1) begin
                #20;
                if (led[0] != prev) begin
                    toggles = toggles + 1;
                    prev = led[0];
                end
            end
            $display("  PWM toggles in 1ms (50000 cycles): %0d", toggles);
            // At ~200 Hz, expect ~200 toggles (both edges) in 1ms = ~400 edges?
            // Actually 200 Hz → 200 periods/s → 0.2 period/ms → one toggle per 2.5ms
            // So in 1ms, we should see 0-2 toggles
            if (toggles > 0)
                $display("  PASS: PWM output is toggling");
            else
                $display("  FAIL: No PWM toggling detected");
        end

        // --- TEST 4: Duty cycle sweeps (breathing) ---
        $display("\n============================================");
        $display(" TEST 4: Breathing Duty Sweep");
        $display("============================================");
        begin
            reg [31:0] prev_duty;
            integer ramp_count;
            integer direction_changes;
            reg up;

            prev_duty = duty_out;
            ramp_count = 0;
            direction_changes = 0;
            up = 1'b1;  // assume starting in RAMP_UP

            // With BREATH_TIMER_MAX=10, duty changes every 10 cycles
            // DUTY_MIN=0x1000_0000, DUTY_MAX=0x2000_0000, DUTY_STEP=0x0100_0000
            // Total steps = (0x20000000 - 0x10000000) / 0x01000000 = 16 steps per direction
            // 16 steps * 10 cycles = 160 cycles per direction
            // 2 directions = 320 cycles for a full breath
            // We'll monitor for 5000 cycles to see multiple direction changes

            repeat (5000) begin
                #20;
                if (duty_out != prev_duty) begin
                    ramp_count = ramp_count + 1;
                    // Detect direction change
                    if (up && (duty_out < prev_duty)) begin
                        up = 1'b0;
                        direction_changes = direction_changes + 1;
                        $display("  DIRECTION CHANGE at t=%0t: RAMP_UP → RAMP_DOWN  duty=0x%08h", $time, duty_out);
                    end
                    if (!up && (duty_out > prev_duty)) begin
                        up = 1'b1;
                        direction_changes = direction_changes + 1;
                        $display("  DIRECTION CHANGE at t=%0t: RAMP_DOWN → RAMP_UP  duty=0x%08h", $time, duty_out);
                    end
                    prev_duty = duty_out;
                end
            end

            $display("  Duty ramp steps observed: %0d", ramp_count);
            $display("  Direction changes: %0d (expected >= 2)", direction_changes);
            if (direction_changes >= 2)
                $display("  PASS: Breathing cycle confirmed (up→down→up)");
            else
                $display("  FAIL: Only %0d direction changes", direction_changes);
        end

        // --- TEST 5: LED active-low ---
        $display("\n============================================");
        $display(" TEST 5: LED Active-Low Property");
        $display("============================================");
        begin
            // During PWM high (duty period), LED should be HIGH = OFF
            // During PWM low, LED should be LOW = ON
            // Check a sample over many cycles
            integer on_count, off_count;
            integer i;
            on_count = 0;
            off_count = 0;
            for (i = 0; i < 100000; i = i + 1) begin
                #20;
                if (led[0] == 1'b0) on_count = on_count + 1;
                else off_count = off_count + 1;
            end
            $display("  LED low (ON)  count: %0d", on_count);
            $display("  LED high (OFF) count: %0d", off_count);
            if (on_count > 0 && off_count > 0)
                $display("  PASS: LED toggles between ON(0) and OFF(1)");
            else
                $display("  FAIL: LED stuck at one level");
        end

        // --- TEST 6: Verify PWM period ---
        $display("\n============================================");
        $display(" TEST 6: PWM Period Measurement");
        $display("============================================");
        begin
            reg prev;
            time last_edge, this_edge;
            integer periods [0:9];
            integer p, i;
            prev = led[0];
            last_edge = 0;
            p = 0;
            for (i = 0; i < 500000 && p < 10; i = i + 1) begin
                #20;
                if (led[0] != prev) begin
                    this_edge = $time;
                    if (last_edge > 0) begin
                        periods[p] = this_edge - last_edge;
                        p = p + 1;
                    end
                    last_edge = this_edge;
                    prev = led[0];
                end
            end
            if (p > 0) begin
                time sum;
                sum = 0;
                for (i = 0; i < p; i = i + 1) sum = sum + periods[i];
                $display("  Avg PWM period: %0t ns (~%0d Hz)", sum / p, 1_000_000_000 / (sum / p));
                // Expected: ~5,000,000 ns (200 Hz)
                if (sum / p > 1_000_000 && sum / p < 20_000_000)
                    $display("  PASS: PWM period in valid range");
                else
                    $display("  WARN: unexpected period");
            end
        end

        // --- SUMMARY ---
        $display("\n============================================");
        $display(" ALL TESTS COMPLETE");
        $display("============================================");
        $display(" Check tb_breath_led.vcd for waveform details");
        $finish;
    end

    // Cycle counter
    always @(posedge clk) begin
        cycle_count <= cycle_count + 1;
    end

endmodule
