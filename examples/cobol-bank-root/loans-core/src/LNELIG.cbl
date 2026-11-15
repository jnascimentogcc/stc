       IDENTIFICATION DIVISION.
       PROGRAM-ID. LNELIG.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
       01  WS-END-AGE              PIC 9(03).
       01  WS-MAX-CMT              PIC 9V99.
       01  WS-CMT-PCT              PIC 9V9999.
           COPY LNCALCP.
       LINKAGE SECTION.
       01  DFHCOMMAREA.
           COPY LNSIMCA.
       PROCEDURE DIVISION.
       0000-MAIN.
           IF CA-RC = '00'
              PERFORM 1000-CHK-AGE
           END-IF
           IF CA-RC = '00'
              PERFORM 2000-CHK-INC
           END-IF
           IF CA-RC = '00'
              PERFORM 3000-CHK-SCORE
           END-IF
           IF CA-RC = '00'
              PERFORM 4000-CALC-INST
              PERFORM 5000-CHK-CMT
           END-IF
           EXEC CICS RETURN END-EXEC.
       1000-CHK-AGE.
           COMPUTE WS-END-AGE = CA-AGE + (CA-TERM-MO + 11) / 12
           IF CA-AGE < 18 OR WS-END-AGE > 80
              MOVE '01' TO CA-RC
           END-IF.
       2000-CHK-INC.
           IF CA-INC-AMT < 1500.00
              MOVE '02' TO CA-RC
           END-IF.
       3000-CHK-SCORE.
           IF CA-SCORE < 300
              MOVE '03' TO CA-RC
           END-IF.
       4000-CALC-INST.
           MOVE CA-PROD-CD   TO LC-PROD-CD
           MOVE CA-PRINC-AMT TO LC-PRINC-AMT
           MOVE CA-RATE-MO   TO LC-RATE-MO
           MOVE CA-TERM-MO   TO LC-TERM-MO
           MOVE CA-AMORT-TP  TO LC-AMORT-TP
           CALL 'LNCALC' USING LNCALC-PARMS
           MOVE LC-INST-AMT  TO CA-INST-AMT
           MOVE LC-IOF-AMT   TO CA-IOF-AMT.
       5000-CHK-CMT.
           EVALUATE CA-PROD-CD
              WHEN 'PY'
                 MOVE 0.35 TO WS-MAX-CMT
              WHEN OTHER
                 MOVE 0.30 TO WS-MAX-CMT
           END-EVALUATE
           COMPUTE WS-CMT-PCT = CA-INST-AMT / CA-INC-AMT
           IF WS-CMT-PCT > WS-MAX-CMT
              MOVE '04' TO CA-RC
           END-IF.
