////////////////////////////////////////////////////////////////////////////////
// Module: breath_led
// Project: hello_fpga — PWM breathing LED
// Target: XC7Z020CLG400-2 (ALINX AX7020, 50 MHz)
//
// Function:
//   PWM-driven LED with breathing effect — duty cycle sweeps from
//   min → max → min in a smooth cycle (~3 seconds total).
//
//   PWM frequency : ~200 Hz  (period_step=17179, N=32, clk=50 MHz)
//   Duty range    : ~10% – ~90% of 2^32
//   Breathing FSM : IDLE → RAMP_UP → RAMP_DOWN → RAMP_UP → ...
//
// Ports:
//   clk      — 50 MHz system clock
//   rst_n    — active-low async reset
//   led[3:0] — LED outputs (active low: 0 = ON, 1 = OFF)
////////////////////////////////////////////////////////////////////////////////
`timescale 1ns / 1ps

module breath_led (
    input  wire       clk,
    input  wire       rst_n,
    output wire [3:0] led,
    output wire [31:0] duty_out      // current PWM duty, exposed for debug
);

    // ----------------------------------------------------------------
    // PWM parameters
    // ----------------------------------------------------------------
    // f_pwm = period_step * f_clk / 2^32
    //       = 17179 * 50e6 / 4,294,967,296 ≈ 200 Hz
    // Parameters for simulation override (default = hardware values)
    parameter PWM_N            = 32;
    parameter PWM_PERIOD_STEP  = 32'd17179;

    // Duty sweep range: ~10% to ~90%
    parameter DUTY_MIN = 32'h1999_9999;
    parameter DUTY_MAX = 32'hE666_6666;
    parameter DUTY_STEP = 32'h0100_0000;  // ~0.4% per step

    // Breathing timing: duty changes every ~5 ms at 50 MHz
    // 5 ms * 50e6 = 250,000 cycles
    parameter BREATH_TIMER_MAX = 32'd250_000;

    // Number of RAMP steps: (DUTY_MAX - DUTY_MIN) / DUTY_STEP ≈ 205
    // Per direction: 205 * 5 ms ≈ 1.0 s
    // Full cycle (up + down): ≈ 2.0 s

    // ----------------------------------------------------------------
    // FSM states
    // ----------------------------------------------------------------
    localparam IDLE      = 2'd0;
    localparam RAMP_UP   = 2'd1;
    localparam RAMP_DOWN = 2'd2;

    // ----------------------------------------------------------------
    // Internals
    // ----------------------------------------------------------------
    reg [1:0]  state;
    reg [31:0] period_cnt;
    reg [31:0] duty;
    reg [31:0] breath_timer;
    reg        pwm_out;
    reg [3:0]  led_r;

    // ----------------------------------------------------------------
    // PWM accumulator
    // ----------------------------------------------------------------
    // period_cnt accumulates by period_step each clock.
    // When period_cnt overflows 32-bit, the accumulator wraps — this
    // naturally creates a 2^N / period_step = 2^32 / 17179 ≈ 250,000
    // clock period, giving a ~200 Hz PWM base frequency.
    //
    // pwm_out is high when period_cnt >= duty (duty controls pulse width)
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            period_cnt <= 32'd0;
        end else begin
            period_cnt <= period_cnt + PWM_PERIOD_STEP;
        end
    end

    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            pwm_out <= 1'b0;
        end else begin
            if (period_cnt >= duty)
                pwm_out <= 1'b1;
            else
                pwm_out <= 1'b0;
        end
    end

    // ----------------------------------------------------------------
    // Breathing FSM — ramps duty cycle up and down
    // ----------------------------------------------------------------
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            state        <= IDLE;
            duty         <= DUTY_MIN;
            breath_timer <= 32'd0;
        end else begin
            case (state)
                IDLE: begin
                    duty  <= DUTY_MIN;
                    state <= RAMP_UP;
                end

                RAMP_UP: begin
                    if (breath_timer >= BREATH_TIMER_MAX - 1) begin
                        breath_timer <= 32'd0;
                        if (duty >= DUTY_MAX - DUTY_STEP) begin
                            state <= RAMP_DOWN;
                        end else begin
                            duty <= duty + DUTY_STEP;
                        end
                    end else begin
                        breath_timer <= breath_timer + 32'd1;
                    end
                end

                RAMP_DOWN: begin
                    if (breath_timer >= BREATH_TIMER_MAX - 1) begin
                        breath_timer <= 32'd0;
                        if (duty <= DUTY_MIN + DUTY_STEP) begin
                            state <= RAMP_UP;
                        end else begin
                            duty <= duty - DUTY_STEP;
                        end
                    end else begin
                        breath_timer <= breath_timer + 32'd1;
                    end
                end

                default: state <= IDLE;
            endcase
        end
    end

    // ----------------------------------------------------------------
    // LED output — active low
    // ----------------------------------------------------------------
    // pwm_out drives the LED: 0 = LED ON, 1 = LED OFF
    // All 4 LEDs share the same breathing pattern
    always @(posedge clk or negedge rst_n) begin
        if (!rst_n) begin
            led_r <= 4'hF;  // all off
        end else begin
            led_r <= {4{pwm_out}};
        end
    end

    assign led = led_r;
    assign duty_out = duty;

endmodule

always @(posedge clk) begin
    pwm_out <= 1'b0;
end
