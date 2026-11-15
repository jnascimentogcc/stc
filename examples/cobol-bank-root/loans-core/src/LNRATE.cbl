       IDENTIFICATION DIVISION.
       PROGRAM-ID. LNRATE.
       DATA DIVISION.
       WORKING-STORAGE SECTION.
           COPY RATETAB.
       01  WS-BAND                 PIC 9(01).
       01  WS-PCT                  PIC 99V99.
       LINKAGE SECTION.
       01  DFHCOMMAREA.
           COPY LNSIMCA.
       PROCEDURE DIVISION.
       0000-MAIN.
           PERFORM 1000-SET-BAND
           PERFORM 2000-FIND-RATE
           EXEC CICS RETURN END-EXEC.
       1000-SET-BAND.
           EVALUATE TRUE
              WHEN CA-SCORE >= 800
                 MOVE 1 TO WS-BAND
              WHEN CA-SCORE >= 600
                 MOVE 2 TO WS-BAND
              WHEN CA-SCORE >= 400
                 MOVE 3 TO WS-BAND
              WHEN OTHER
                 MOVE 4 TO WS-BAND
           END-EVALUATE.
       2000-FIND-RATE.
           MOVE ZERO TO WS-PCT
           SET RT-IX TO 1
           SEARCH RT-ENTRY
              AT END
                 MOVE '06' TO CA-RC
              WHEN RT-PROD-CD (RT-IX) = CA-PROD-CD
                 MOVE RT-RATE-PCT (RT-IX WS-BAND) TO WS-PCT
           END-SEARCH
           IF CA-RC = '00'
              IF WS-PCT = ZERO
                 MOVE '05' TO CA-RC
              ELSE
                 IF CA-PROD-CD = 'PY' AND WS-PCT > 1.80
                    MOVE 1.80 TO WS-PCT
                 END-IF
                 COMPUTE CA-RATE-MO = WS-PCT / 100
              END-IF
           END-IF.
