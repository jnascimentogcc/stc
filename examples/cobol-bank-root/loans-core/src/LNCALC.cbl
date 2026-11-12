       IDENTIFICATION DIVISION.
       PROGRAM-ID. LNCALC.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01  WS-FCT                  COMP-2.
       01  WS-IOF-DAYS             PIC 9(05).
       LINKAGE SECTION.
           COPY LNCALCP.
       PROCEDURE DIVISION USING LNCALC-PARMS.
       0000-MAIN.
           IF LC-AMORT-TP = 'S'
              PERFORM 2000-SAC
           ELSE
              PERFORM 1000-PRICE
           END-IF
           PERFORM 3000-IOF
           GOBACK.
       1000-PRICE.
           COMPUTE WS-FCT = (1 + LC-RATE-MO) ** LC-TERM-MO
           COMPUTE LC-INST-AMT ROUNDED =
                   LC-PRINC-AMT * LC-RATE-MO * WS-FCT / (WS-FCT - 1).
      * SAC: FIRST INSTALLMENT ONLY
       2000-SAC.
           COMPUTE LC-INST-AMT ROUNDED =
                   LC-PRINC-AMT / LC-TERM-MO
                 + LC-PRINC-AMT * LC-RATE-MO.
       3000-IOF.
           IF LC-PROD-CD = 'MT'
              MOVE ZERO TO LC-IOF-AMT
           ELSE
              COMPUTE WS-IOF-DAYS = LC-TERM-MO * 30
              IF WS-IOF-DAYS > 365
                 MOVE 365 TO WS-IOF-DAYS
              END-IF
              COMPUTE LC-IOF-AMT ROUNDED =
                      LC-PRINC-AMT * 0.0038
                    + LC-PRINC-AMT * 0.000082 * WS-IOF-DAYS
           END-IF.
