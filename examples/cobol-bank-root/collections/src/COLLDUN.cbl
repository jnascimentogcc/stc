       IDENTIFICATION DIVISION.
       PROGRAM-ID. COLLDUN.
      *----------------------------------------------------------------
      * DUNNING - JOB COLLJOB
      * STAGES: R1 SMS   R2 LETTER   R3 CALL CENTER
      *         PD PAYROLL DEDUCTION REQUEST TO EMPLOYER
      *         AG EXTERNAL AGENCY
      *----------------------------------------------------------------
       ENVIRONMENT DIVISION.
       INPUT-OUTPUT SECTION.
       FILE-CONTROL.
           SELECT LATE-IN  ASSIGN TO LATEIN.
           SELECT DUNN-OUT ASSIGN TO DUNNOUT.
       DATA DIVISION.
       FILE SECTION.
       FD  LATE-IN RECORDING MODE F.
           COPY LATEREC.
       FD  DUNN-OUT RECORDING MODE F.
       01  DUNN-REC.
           05  DN-CONTR-ID         PIC 9(10).
           05  DN-INST-NO          PIC 9(03).
           05  DN-STAGE            PIC X(02).
           05  DN-DUE-AMT          PIC 9(11)V99.
           05  DN-FEE-AMT          PIC 9(11)V99.
           05  DN-BUREAU           PIC X(01).
       WORKING-STORAGE SECTION.
       01  WS-EOF                  PIC X(01) VALUE 'N'.
       01  WS-DUE                  PIC 9(11)V99.
       PROCEDURE DIVISION.
       0000-MAIN.
           OPEN INPUT LATE-IN OUTPUT DUNN-OUT
           PERFORM 1000-READ
           PERFORM 2000-PROCESS UNTIL WS-EOF = 'Y'
           CLOSE LATE-IN DUNN-OUT
           STOP RUN.
       1000-READ.
           READ LATE-IN
              AT END
                 MOVE 'Y' TO WS-EOF
           END-READ.
       2000-PROCESS.
           COMPUTE WS-DUE = LR-INST-AMT + LR-MORA-AMT + LR-FINE-AMT
           MOVE LR-CONTR-ID TO DN-CONTR-ID
           MOVE LR-INST-NO  TO DN-INST-NO
           MOVE WS-DUE      TO DN-DUE-AMT
           MOVE ZERO        TO DN-FEE-AMT
           MOVE 'N'         TO DN-BUREAU
           EVALUATE TRUE
              WHEN LR-DAYS-LATE <= 30
                 MOVE 'R1' TO DN-STAGE
              WHEN LR-DAYS-LATE <= 60
                 MOVE 'R2' TO DN-STAGE
              WHEN LR-DAYS-LATE <= 90
                 MOVE 'R3' TO DN-STAGE
              WHEN LR-PROD-CD = 'PY'
                 MOVE 'PD' TO DN-STAGE
              WHEN OTHER
                 MOVE 'AG' TO DN-STAGE
                 COMPUTE DN-FEE-AMT ROUNDED = WS-DUE * 0.10
           END-EVALUATE
           IF LR-DAYS-LATE > 120 AND LR-PROD-CD NOT = 'PY'
              MOVE 'Y' TO DN-BUREAU
           END-IF
           WRITE DUNN-REC
           PERFORM 1000-READ.
