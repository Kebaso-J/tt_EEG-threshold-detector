/*
 * Copyright (c) 2024 Jacob Kebaso
 * SPDX-License-Identifier: Apache-2.0
 */

// ----------------------------------------------------------------------
// tt_um_eeg_threshold_detector
// ----------------------------------------------------------------------
//
// Digital threshold-crossing event detector with hysteresis and a refractory period --
// a "digital Schmitt trigger" intended as the event-detection stage downstream of an external
// ADC digitizing an EEG signal.
//
// No analog pins are used. ui_in doubles as the sample bus (normal operation)
// and the configuration-write data bus (when cfg_mode is asserted on uio_in).
//
// Pin mapping
// ----------------------------------------------------------------------
// ui_in[7:0]   : digitized sample value (cfg_mode = 0)
//                configuration value to write (cfg_mode = 1)
// uio_in[0]    : cfg_mode  - 1 = configuration write cycle, 0 = normal sample cycle
// uio_in[2:1]  : cfg_addr  - selects which config register ui_in is written to
//                  2'd0 -> th_high        (arm/detect threshold)
//                  2'd1 -> th_low         (re-arm/hysteresis threshold, must be < th_high)
//                  2'd2 -> debounce_limit (# consecutive samples above th_high to trigger)
//                  2'd3 -> refractory_len (# cycles held in refractory after detection clears)
// uio_in[7:3]  : unused
// uio_out      : unused, driven low
// uio_oe       : all zero (uio pins are inputs only)
//
// uo_out[0]    : event_detected  - asserted while state == DETECTED
// uo_out[1]    : armed           - asserted while state == MONITOR
// uo_out[2]    : above_high      - raw comparator output (debug), sample >= th_high
// uo_out[3]    : in_refractory   - asserted while state == REFRACTORY
// uo_out[7:4]  : unused, driven low
// ----------------------------------------------------------------------


`default_nettype none

module tt_um_eeg_threshold_detector (
  input wire [7:0] ui_in,    
  output wire [7:0] uo_out,  
  input wire [7:0] uio_in,   
  output wire [7:0] uio_out,
  output wire [7:0] uio_oe,
  input wire ena,
  input wire clk,
  input wire rst_n
);

  // ----------------------------------------------------------------------
  // Configuration registers (defaults chosen so the design is meaningfully armed
  // immediately out of reset, before any config writes happen)
  // ----------------------------------------------------------------------
  reg [7:0] th_high;
  reg [7:0] th_low;
  reg [7:0] debounce_limit;
  reg [7:0] refractory_len;

  wire cfg_mode = uio_in[0];
  wire [1:0] cfg_addr = uio_in[2:1];

  // ----------------------------------------------------------------------
  // FSM
  // ----------------------------------------------------------------------
  localparam MONITOR = 2'd0;
  localparam DETECTED = 2'd1;
  localparam REFRACTORY = 2'd2;

  reg [1:0] state;
  reg [7:0] above_count;
  reg [7:0] refractory_count;

  wire above_high = (ui_in >= th_high);
  wire below_low = (ui_in <= th_low);

  always @(posedge clk or negedge rst_n) begin
    if (!rst_n) begin
        th_high         <= 8'd200;  // default arm/detect threshold
        th_low          <= 8'd150;  // default re-arm/hysteresis threshold
        debounce_limit  <= 8'd4;    // default # consecutive samples above th_high to trigger
        refractory_len  <= 8'd32;   // default # cycles held in refractory after detection clears
        state           <= MONITOR;
        above_count     <= 8'd0;
        refractory_count <= 8'd0;
    end else if (ena) begin
        if (cfg_mode) begin
            // Configuration write cycle: ui_in is treated as the value
            case (cfg_addr)
                2'd0: th_high     <= ui_in;
                2'd1: th_low      <= ui_in;
                2'd2: debounce_limit <= ui_in;
                2'd3: refractory_len <= ui_in;
                default: ;
            endcase
        end else begin
            // Normal sample-processing cycle
            case (state)
                MONITOR: begin
                    if (above_high) begin
                        if (above_count >= debounce_limit) begin
                            state       <= DETECTED;
                            above_count <= 8'd0;
                        end else begin
                            above_count <= above_count + 8'd1;
                        end
                    end else begin
                        above_count <= 8'd0;
                    end
                end

                DETECTED: begin
                    if (below_low) begin
                        state <= REFRACTORY;
                        refractory_count <= refractory_len;
                    end
                end

                REFRACTORY: begin
                    if (refractory_count == 8'd0) begin
                        state <= MONITOR;
                    end else begin
                        refractory_count <= refractory_count - 8'd1;
                    end
                end

                default: state <= MONITOR;
            endcase
        end
    end
  end

  // ----------------------------------------------------------------------
  // Output 
  // ----------------------------------------------------------------------
  assign uo_out[0] = (state == DETECTED);      // event_detected
  assign uo_out[1] = (state == MONITOR);       // armed
  assign uo_out[2] = above_high;               // above_high (raw comparator output)
  assign uo_out[3] = (state == REFRACTORY);    // in_refractory
  assign uo_out[7:4] = 4'b0;  

  assign uio_out = 8'b0;  // uio_out is unused
  assign uio_oe  = 8'b0;  // uio pins are inputs

  // Intentionally unused inputs to prevent warnings from the linter
  wire _unused = &{ena, uio_in[7:3], 1'b0};

endmodule                
