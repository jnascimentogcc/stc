       IDENTIFICATION DIVISION.
       PROGRAM-ID. LNSIMUL.
      *----------------------------------------------------------------
      * LOAN SIMULATION - CICS TRANSACTION LNSI
      *----------------------------------------------------------------
       DATA DIVISION.
       WORKING-STORAGE SECTION.
           EXEC SQL INCLUDE SQLCA END-EXEC.
           COPY CUSTREC.
       01  WS-TODAY                PIC 9(08).
       LINKAGE SECTION.
       01  DFHCOMMAREA.
           COPY LNSIMCA.
       PROCEDURE DIVISION.
       0000-MAIN.
           MOVE '00' TO CA-RC
           PERFORM 1000-GET-CUST
           IF CA-RC = '00'
              EXEC CICS LINK PROGRAM('LNRATE')
                   COMMAREA(DFHCOMMAREA)
              END-EXEC
           END-IF
           IF CA-RC = '00'
              EXEC CICS LINK PROGRAM('LNELIG')
                   COMMAREA(DFHCOMMAREA)
              END-EXEC
           END-IF
           EXEC CICS RETURN END-EXEC.
       1000-GET-CUST.
           EXEC SQL
              SELECT BIRTH_DT, MONTHLY_INC, CREDIT_SCORE
                INTO :CU-BIRTH-DT, :CU-INC-AMT, :CU-SCORE
                FROM CUSTOMER
               WHERE CUST_ID = :CA-CUST-ID
           END-EXEC
           IF SQLCODE NOT = 0
              MOVE '07' TO CA-RC
           ELSE
              MOVE FUNCTION CURRENT-DATE (1:8) TO WS-TODAY
              COMPUTE CA-AGE = (WS-TODAY - CU-BIRTH-DT) / 10000
              MOVE CU-INC-AMT TO CA-INC-AMT
              MOVE CU-SCORE   TO CA-SCORE
           END-IF.
